#define R_LOG_ORIGIN "core.r2a"

#include <r_core.h>
#include <r_util/r_json.h>
#include <r_util/pj.h>

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>

#ifdef _WIN32
#include <windows.h>
#else
#include <unistd.h>
#include <fcntl.h>
#include <signal.h>
#include <sys/wait.h>
#include <pthread.h>
#endif

#define R2A_VERSION "0.3.3"
#define R2A_TIMEOUT_MS 30000
#define R2A_ASK_TIMEOUT_MS 120000

typedef struct r2a_msg_t {
	char *raw;
	RJson *json;
	struct r2a_msg_t *next;
} R2AMsg;

typedef struct r2a_t {
#ifdef _WIN32
	HANDLE proc;
	HANDLE stdin_wr;
	HANDLE stdout_rd;
	HANDLE stderr_rd;
	HANDLE reader_thread;
	HANDLE stderr_thread;
	CRITICAL_SECTION lock;
	CRITICAL_SECTION queue_lock;
	HANDLE queue_event;
	HANDLE cancel_event;
#else
	pid_t pid;
	int stdin_fd;
	int stdout_fd;
	int stderr_fd;
	pthread_t reader_thread;
	pthread_t stderr_thread;
	pthread_mutex_t lock;
	pthread_mutex_t queue_lock;
	pthread_cond_t queue_cond;
	volatile bool cancelled;
#endif
	volatile bool running;
	bool in_request;
	int request_id;
	char *binary_name;

	R2AMsg *queue_head;
	R2AMsg *queue_tail;

	RCore *core;
	RStrBuf *sb;
} R2A;

typedef struct {
	R2A *agent;
} R2AData;

static R2A *g_current_agent = NULL;

static R2A *r2a_new(void);
static void r2a_free(R2A *a);
static bool r2a_start(R2A *a, RCore *core);
static void r2a_stop(R2A *a);
static bool r2a_is_running(R2A *a);
static void r2a_cancel(R2A *a);
static void r2a_ask(R2A *a, const char *prompt);
static R2AMsg *r2a_session_list(R2A *a, bool filter_binary);
static R2AMsg *r2a_session_new(R2A *a);
static R2AMsg *r2a_session_switch(R2A *a, const char *session_id);
static R2AMsg *r2a_session_delete(R2A *a, const char *session_id);

// Message Queue

static void queue_push(R2A *a, char *raw, RJson *json) {
	R2AMsg *msg = R_NEW0(R2AMsg);
	if (!msg) {
		free(raw);
		if (json) r_json_free(json);
		return;
	}
	msg->raw = raw;
	msg->json = json;
	msg->next = NULL;

#ifdef _WIN32
	EnterCriticalSection(&a->queue_lock);
	if (a->queue_tail) {
		a->queue_tail->next = msg;
	} else {
		a->queue_head = msg;
	}
	a->queue_tail = msg;
	SetEvent(a->queue_event);
	LeaveCriticalSection(&a->queue_lock);
#else
	pthread_mutex_lock(&a->queue_lock);
	if (a->queue_tail) {
		a->queue_tail->next = msg;
	} else {
		a->queue_head = msg;
	}
	a->queue_tail = msg;
	pthread_cond_signal(&a->queue_cond);
	pthread_mutex_unlock(&a->queue_lock);
#endif
}

static R2AMsg *queue_pop(R2A *a, int timeout_ms) {
	R2AMsg *msg = NULL;

#ifdef _WIN32
	DWORD wait_time = (timeout_ms < 0) ? INFINITE : (DWORD)timeout_ms;
	HANDLE wait_handles[2] = { a->queue_event, a->cancel_event };
	while (!msg && a->running) {
		EnterCriticalSection(&a->queue_lock);
		if (a->queue_head) {
			msg = a->queue_head;
			a->queue_head = msg->next;
			if (!a->queue_head) a->queue_tail = NULL;
			msg->next = NULL;
		}
		LeaveCriticalSection(&a->queue_lock);
		if (!msg) {
			DWORD res = WaitForMultipleObjects(2, wait_handles, FALSE, wait_time);
			if (res == WAIT_TIMEOUT || res == WAIT_OBJECT_0 + 1) break;
		}
	}
#else
	struct timespec ts;
	clock_gettime(CLOCK_REALTIME, &ts);
	ts.tv_sec += timeout_ms / 1000;
	ts.tv_nsec += (timeout_ms % 1000) * 1000000;
	if (ts.tv_nsec >= 1000000000) {
		ts.tv_sec++;
		ts.tv_nsec -= 1000000000;
	}

	pthread_mutex_lock(&a->queue_lock);
	while (!a->queue_head && a->running && !a->cancelled) {
		if (timeout_ms < 0) {
			pthread_cond_wait(&a->queue_cond, &a->queue_lock);
		} else {
			int rc = pthread_cond_timedwait(&a->queue_cond, &a->queue_lock, &ts);
			if (rc == ETIMEDOUT) break;
		}
	}
	if (a->queue_head) {
		msg = a->queue_head;
		a->queue_head = msg->next;
		if (!a->queue_head) a->queue_tail = NULL;
		msg->next = NULL;
	}
	pthread_mutex_unlock(&a->queue_lock);
#endif

	return msg;
}

static void queue_clear(R2A *a) {
#ifdef _WIN32
	EnterCriticalSection(&a->queue_lock);
#else
	pthread_mutex_lock(&a->queue_lock);
#endif
	while (a->queue_head) {
		R2AMsg *msg = a->queue_head;
		a->queue_head = msg->next;
		free(msg->raw);
		if (msg->json) r_json_free(msg->json);
		free(msg);
	}
	a->queue_tail = NULL;
#ifdef _WIN32
	LeaveCriticalSection(&a->queue_lock);
#else
	pthread_mutex_unlock(&a->queue_lock);
#endif
}

static void msg_free(R2AMsg *msg) {
	if (msg) {
		free(msg->raw);
		if (msg->json) r_json_free(msg->json);
		free(msg);
	}
}

// Reader Threads

#ifdef _WIN32
static DWORD WINAPI stdout_reader(LPVOID arg) {
	R2A *a = (R2A *)arg;
	char buf[8192];
	char *line_buf = NULL;
	size_t line_len = 0, line_cap = 0;
	DWORD n;

	while (a->running) {
		if (!ReadFile(a->stdout_rd, buf, sizeof(buf) - 1, &n, NULL) || n == 0) break;
		buf[n] = '\0';

		if (line_len + n + 1 > line_cap) {
			line_cap = (line_len + n + 1) * 2;
			line_buf = realloc(line_buf, line_cap);
			if (!line_buf) break;
		}
		memcpy(line_buf + line_len, buf, n + 1);
		line_len += n;

		char *start = line_buf;
		char *nl;
		while ((nl = strchr(start, '\n'))) {
			*nl = '\0';
			if (nl > start && *(nl - 1) == '\r') *(nl - 1) = '\0';

			if (start[0] == '{') {
				char *raw = strdup(start);
				RJson *json = raw ? r_json_parse(raw) : NULL;
				if (json) {
					queue_push(a, raw, json);
				} else {
					free(raw);
					if (a->core) printf("%s\n", start);
				}
			} else if (start[0]) {
				if (a->core) {
					printf("%s\n", start);
					fflush(stdout);
				}
			}
			start = nl + 1;
		}

		size_t rem = strlen(start);
		if (rem > 0) memmove(line_buf, start, rem + 1);
		line_len = rem;
	}
	free(line_buf);
	return 0;
}

static DWORD WINAPI stderr_reader(LPVOID arg) {
	R2A *a = (R2A *)arg;
	char buf[4096];
	DWORD n;
	while (a->running) {
		if (!ReadFile(a->stderr_rd, buf, sizeof(buf) - 1, &n, NULL) || n == 0) break;
		buf[n] = '\0';
		R_LOG_ERROR("%s", buf);
	}
	return 0;
}
#else
static void *stdout_reader(void *arg) {
	R2A *a = (R2A *)arg;
	char buf[8192];
	char *line_buf = NULL;
	size_t line_len = 0, line_cap = 0;

	while (a->running) {
		ssize_t n = read(a->stdout_fd, buf, sizeof(buf) - 1);
		if (n <= 0) break;
		buf[n] = '\0';

		if (line_len + n + 1 > line_cap) {
			line_cap = (line_len + n + 1) * 2;
			line_buf = realloc(line_buf, line_cap);
			if (!line_buf) break;
		}
		memcpy(line_buf + line_len, buf, n + 1);
		line_len += n;

		char *start = line_buf;
		char *nl;
		while ((nl = strchr(start, '\n'))) {
			*nl = '\0';
			if (nl > start && *(nl - 1) == '\r') *(nl - 1) = '\0';

			if (start[0] == '{') {
				char *raw = strdup(start);
				RJson *json = raw ? r_json_parse(raw) : NULL;
				if (json) {
					queue_push(a, raw, json);
				} else {
					free(raw);
					if (a->core) printf("%s\n", start);
				}
			} else if (start[0]) {
				if (a->core) {
					printf("%s\n", start);
					fflush(stdout);
				}
			}
			start = nl + 1;
		}

		size_t rem = strlen(start);
		if (rem > 0) memmove(line_buf, start, rem + 1);
		line_len = rem;
	}
	free(line_buf);
	return NULL;
}

static void *stderr_reader(void *arg) {
	R2A *a = (R2A *)arg;
	char buf[4096];
	while (a->running) {
		ssize_t n = read(a->stderr_fd, buf, sizeof(buf) - 1);
		if (n <= 0) break;
		buf[n] = '\0';
		R_LOG_ERROR("%s", buf);
	}
	return NULL;
}
#endif

// Subprocess Management

#ifdef _WIN32
static bool spawn_process(R2A *a) {
	SECURITY_ATTRIBUTES sa = { sizeof(SECURITY_ATTRIBUTES), NULL, TRUE };
	HANDLE stdin_rd, stdout_wr, stderr_wr;

	if (!CreatePipe(&stdin_rd, &a->stdin_wr, &sa, 0)) return false;
	if (!CreatePipe(&a->stdout_rd, &stdout_wr, &sa, 0)) { CloseHandle(stdin_rd); CloseHandle(a->stdin_wr); return false; }
	if (!CreatePipe(&a->stderr_rd, &stderr_wr, &sa, 0)) { CloseHandle(stdin_rd); CloseHandle(a->stdin_wr); CloseHandle(a->stdout_rd); CloseHandle(stdout_wr); return false; }

	SetHandleInformation(a->stdin_wr, HANDLE_FLAG_INHERIT, 0);
	SetHandleInformation(a->stdout_rd, HANDLE_FLAG_INHERIT, 0);
	SetHandleInformation(a->stderr_rd, HANDLE_FLAG_INHERIT, 0);

	STARTUPINFOA si = { sizeof(STARTUPINFOA) };
	si.dwFlags = STARTF_USESTDHANDLES;
	si.hStdInput = stdin_rd;
	si.hStdOutput = stdout_wr;
	si.hStdError = stderr_wr;

	PROCESS_INFORMATION pi;
	char cmdline[] = "r2a stdio";
	if (!CreateProcessA(NULL, cmdline, NULL, NULL, TRUE, CREATE_NO_WINDOW, NULL, NULL, &si, &pi)) {
		CloseHandle(stdin_rd); CloseHandle(a->stdin_wr);
		CloseHandle(stdout_wr); CloseHandle(a->stdout_rd);
		CloseHandle(stderr_wr); CloseHandle(a->stderr_rd);
		return false;
	}

	CloseHandle(stdin_rd);
	CloseHandle(stdout_wr);
	CloseHandle(stderr_wr);
	CloseHandle(pi.hThread);
	a->proc = pi.hProcess;
	return true;
}

static void kill_process(R2A *a) {
	if (a->proc) {
		TerminateProcess(a->proc, 0);
		WaitForSingleObject(a->proc, 2000);
		CloseHandle(a->proc);
		a->proc = NULL;
	}
	if (a->stdin_wr) { CloseHandle(a->stdin_wr); a->stdin_wr = NULL; }
	if (a->stdout_rd) { CloseHandle(a->stdout_rd); a->stdout_rd = NULL; }
	if (a->stderr_rd) { CloseHandle(a->stderr_rd); a->stderr_rd = NULL; }
}
#else
static bool spawn_process(R2A *a) {
	int stdin_pipe[2], stdout_pipe[2], stderr_pipe[2];

	if (pipe(stdin_pipe) < 0) return false;
	if (pipe(stdout_pipe) < 0) { close(stdin_pipe[0]); close(stdin_pipe[1]); return false; }
	if (pipe(stderr_pipe) < 0) { close(stdin_pipe[0]); close(stdin_pipe[1]); close(stdout_pipe[0]); close(stdout_pipe[1]); return false; }

	pid_t pid = fork();
	if (pid < 0) {
		close(stdin_pipe[0]); close(stdin_pipe[1]);
		close(stdout_pipe[0]); close(stdout_pipe[1]);
		close(stderr_pipe[0]); close(stderr_pipe[1]);
		return false;
	}

	if (pid == 0) {
		close(stdin_pipe[1]);
		close(stdout_pipe[0]);
		close(stderr_pipe[0]);
		dup2(stdin_pipe[0], STDIN_FILENO);
		dup2(stdout_pipe[1], STDOUT_FILENO);
		dup2(stderr_pipe[1], STDERR_FILENO);
		close(stdin_pipe[0]);
		close(stdout_pipe[1]);
		close(stderr_pipe[1]);
		execlp("r2a", "r2a", "stdio", NULL);
		_exit(127);
	}

	close(stdin_pipe[0]);
	close(stdout_pipe[1]);
	close(stderr_pipe[1]);
	a->pid = pid;
	a->stdin_fd = stdin_pipe[1];
	a->stdout_fd = stdout_pipe[0];
	a->stderr_fd = stderr_pipe[0];

	return true;
}

static void kill_process(R2A *a) {
	if (a->pid > 0) {
		kill(a->pid, SIGTERM);
		int status;
		int waited = 0;
		while (waited < 2000) {
			pid_t ret = waitpid(a->pid, &status, WNOHANG);
			if (ret != 0) break;
			usleep(50000);
			waited += 50;
		}
		if (waited >= 2000) {
			kill(a->pid, SIGKILL);
			waitpid(a->pid, &status, 0);
		}
		a->pid = 0;
	}
	if (a->stdin_fd >= 0) { close(a->stdin_fd); a->stdin_fd = -1; }
	if (a->stdout_fd >= 0) { close(a->stdout_fd); a->stdout_fd = -1; }
	if (a->stderr_fd >= 0) { close(a->stderr_fd); a->stderr_fd = -1; }
}
#endif

// R2A Lifecycle

static R2A *r2a_new(void) {
	R2A *a = R_NEW0(R2A);
	if (!a) return NULL;

#ifdef _WIN32
	InitializeCriticalSection(&a->lock);
	InitializeCriticalSection(&a->queue_lock);
	a->queue_event = CreateEvent(NULL, FALSE, FALSE, NULL);
	a->cancel_event = CreateEvent(NULL, TRUE, FALSE, NULL);
#else
	pthread_mutex_init(&a->lock, NULL);
	pthread_mutex_init(&a->queue_lock, NULL);
	pthread_cond_init(&a->queue_cond, NULL);
	a->pid = 0;
	a->stdin_fd = -1;
	a->stdout_fd = -1;
	a->stderr_fd = -1;
#endif

	return a;
}

static void r2a_free(R2A *a) {
	if (!a) return;
	r2a_stop(a);
	queue_clear(a);
	free(a->binary_name);
	r_strbuf_free(a->sb);
	a->sb = NULL;

#ifdef _WIN32
	DeleteCriticalSection(&a->lock);
	DeleteCriticalSection(&a->queue_lock);
	CloseHandle(a->queue_event);
	CloseHandle(a->cancel_event);
#else
	pthread_mutex_destroy(&a->lock);
	pthread_mutex_destroy(&a->queue_lock);
	pthread_cond_destroy(&a->queue_cond);
#endif

	free(a);
}

static char *get_binary_name(RCore *core) {
	if (!core) return strdup("unknown");

	char *info = r_core_cmd_str(core, "ij");
	if (!info) return strdup("unknown");

	RJson *root = r_json_parse(info);
	if (!root) { free(info); return strdup("unknown"); }

	const char *path = NULL;
	const RJson *jcore = r_json_get(root, "core");
	if (jcore) {
		const RJson *jfile = r_json_get(jcore, "file");
		if (jfile && jfile->str_value) path = jfile->str_value;
	}
	if (!path) {
		const RJson *jbin = r_json_get(root, "bin");
		if (jbin) {
			const RJson *jfile = r_json_get(jbin, "file");
			if (jfile && jfile->str_value) path = jfile->str_value;
		}
	}

	char *result;
	if (path) {
		const char *base = r_str_rchr(path, NULL, '/');
		if (!base) base = r_str_rchr(path, NULL, '\\');
		result = strdup(base ? base + 1 : path);
	} else {
		result = strdup("unknown");
	}

	r_json_free(root);
	free(info);
	return result;
}

static bool logcb(void *user, int type, const char *origin, const char *msg) {
	if (type > R_LOG_LEVEL_WARN) {
		return false;
	}
	if (!msg || R_STR_ISEMPTY(origin)) {
		return true;
	}
	R2A *a = (R2A *)user;
	if (a->sb) {
		const char *typestr = r_log_level_tostring(type);
		r_strbuf_appendf(a->sb, "[%s] %s\n", typestr, msg);
	}
	return true;
}

static void r2a_log_reset(R2A *a) {
	r_strbuf_free(a->sb);
	a->sb = r_strbuf_new("");
}

static char *r2a_log_drain(R2A *a) {
	char *s = r_strbuf_drain(a->sb);
	if (R_STR_ISNOTEMPTY(s)) {
		a->sb = NULL;
		return s;
	}
	free(s);
	a->sb = NULL;
	return NULL;
}

static void r2a_send_message(R2A *a, const char *json_str) {
	if (!a || !a->running) return;
	size_t len = strlen(json_str);

#ifdef _WIN32
	DWORD written;
	WriteFile(a->stdin_wr, json_str, (DWORD)len, &written, NULL);
	WriteFile(a->stdin_wr, "\n", 1, &written, NULL);
#else
	write(a->stdin_fd, json_str, len);
	write(a->stdin_fd, "\n", 1);
#endif
}

static R2AMsg *r2a_send_request(R2A *a, const char *method, const char *params_json, int timeout_ms) {
	if (!a || !a->running) return NULL;

#ifdef _WIN32
	EnterCriticalSection(&a->lock);
	int req_id = ++a->request_id;
	LeaveCriticalSection(&a->lock);
#else
	pthread_mutex_lock(&a->lock);
	int req_id = ++a->request_id;
	pthread_mutex_unlock(&a->lock);
#endif

	PJ *pj = pj_new();
	pj_o(pj);
	pj_ks(pj, "jsonrpc", "2.0");
	pj_ks(pj, "method", method);
	pj_ki(pj, "id", req_id);
	if (params_json && params_json[0]) {
		pj_k(pj, "params");
		pj_raw(pj, params_json);
	}
	pj_end(pj);
	char *msg = pj_drain(pj);

	r2a_send_message(a, msg);
	free(msg);

	while (a->running) {
		R2AMsg *m = queue_pop(a, timeout_ms);
		if (!m) {
			R_LOG_ERROR("Timeout waiting for response to %s", method);
			return NULL;
		}

		const RJson *jid = r_json_get(m->json, "id");
		if (jid && jid->type == R_JSON_INTEGER && jid->num.s_value == req_id) {
			m->next = NULL;
			return m;
		}

		queue_push(a, m->raw, m->json);
		m->raw = NULL;
		m->json = NULL;
		free(m);
	}

	return NULL;
}

static bool r2a_start(R2A *a, RCore *core) {
	if (a->running) return true;

	a->core = core;

#ifdef _WIN32
	ResetEvent(a->cancel_event);
#else
	a->cancelled = false;
#endif

	if (!spawn_process(a)) {
		R_LOG_ERROR("r2agent server not found. See https://nitanmarcel.github.io/r2agent/setup.html");
		return false;
	}

	a->running = true;
	r_log_add_callback(logcb, a);

#ifdef _WIN32
	a->reader_thread = CreateThread(NULL, 0, stdout_reader, a, 0, NULL);
	a->stderr_thread = CreateThread(NULL, 0, stderr_reader, a, 0, NULL);
#else
	pthread_create(&a->reader_thread, NULL, stdout_reader, a);
	pthread_create(&a->stderr_thread, NULL, stderr_reader, a);
#endif

	free(a->binary_name);
	a->binary_name = get_binary_name(core);

	PJ *pj = pj_new();
	pj_o(pj);
	pj_ks(pj, "client_version", R2A_VERSION);
	pj_ks(pj, "binary_name", a->binary_name);
	pj_end(pj);
	char *params = pj_drain(pj);

	R2AMsg *resp = r2a_send_request(a, "initialize", params, R2A_TIMEOUT_MS);
	free(params);

	if (!resp) {
		r2a_stop(a);
		return false;
	}

	const RJson *jerr = r_json_get(resp->json, "error");
	if (jerr) {
		const RJson *jmsg = r_json_get(jerr, "message");
		R_LOG_ERROR("Initialization failed: %s", jmsg && jmsg->str_value ? jmsg->str_value : "Unknown error");
		msg_free(resp);
		r2a_stop(a);
		return false;
	}

	const RJson *jresult = r_json_get(resp->json, "result");
	if (jresult) {
		const RJson *jver = r_json_get(jresult, "server_version");
		if (jver && jver->str_value && strcmp(jver->str_value, R2A_VERSION) != 0) {
			R_LOG_WARN("Version mismatch - plugin=%s, server=%s", R2A_VERSION, jver->str_value);
		}
	}

	msg_free(resp);
	return true;
}

static void r2a_stop(R2A *a) {
	if (!a->running) return;

	r_log_del_callback(logcb);
	r2a_send_message(a, "{\"jsonrpc\":\"2.0\",\"method\":\"shutdown\",\"id\":0}");

	a->running = false;

#ifdef _WIN32
	SetEvent(a->cancel_event);
	SetEvent(a->queue_event);
#else
	a->cancelled = true;
	pthread_cond_broadcast(&a->queue_cond);
#endif

	kill_process(a);

#ifdef _WIN32
	if (a->reader_thread) { WaitForSingleObject(a->reader_thread, 1000); CloseHandle(a->reader_thread); a->reader_thread = NULL; }
	if (a->stderr_thread) { WaitForSingleObject(a->stderr_thread, 1000); CloseHandle(a->stderr_thread); a->stderr_thread = NULL; }
#else
	pthread_join(a->reader_thread, NULL);
	pthread_join(a->stderr_thread, NULL);
#endif

	queue_clear(a);
}

static bool r2a_is_running(R2A *a) {
	if (!a || !a->running) return false;
#ifdef _WIN32
	DWORD code;
	return GetExitCodeProcess(a->proc, &code) && code == STILL_ACTIVE;
#else
	return a->pid > 0 && kill(a->pid, 0) == 0;
#endif
}

static void r2a_cancel(R2A *a) {
#ifdef _WIN32
	SetEvent(a->cancel_event);
#else
	a->cancelled = true;
	pthread_cond_broadcast(&a->queue_cond);
#endif
	r2a_send_message(a, "{\"jsonrpc\":\"2.0\",\"method\":\"cancel\"}");
}

// Ask (main AI interaction)

#ifndef _WIN32
static void sigint_handler(int sig) {
	(void)sig;
	if (g_current_agent) r2a_cancel(g_current_agent);
}
#endif

static void r2a_ask(R2A *a, const char *prompt) {
	if (!a || !a->running || !a->core) return;

#ifdef _WIN32
	ResetEvent(a->cancel_event);
#else
	a->cancelled = false;
#endif
	a->in_request = true;

#ifdef _WIN32
	EnterCriticalSection(&a->lock);
	int req_id = ++a->request_id;
	LeaveCriticalSection(&a->lock);
#else
	pthread_mutex_lock(&a->lock);
	int req_id = ++a->request_id;
	pthread_mutex_unlock(&a->lock);
#endif

	PJ *pj = pj_new();
	pj_o(pj);
	pj_ks(pj, "jsonrpc", "2.0");
	pj_ks(pj, "method", "ask");
	pj_ki(pj, "id", req_id);
	pj_k(pj, "params");
	pj_o(pj);
	pj_ks(pj, "prompt", prompt);
	pj_end(pj);
	pj_end(pj);
	char *msg = pj_drain(pj);

	r2a_send_message(a, msg);
	free(msg);

	bool cancelled = false;

#ifndef _WIN32
	g_current_agent = a;
	struct sigaction sa, old_sa;
	memset(&sa, 0, sizeof(sa));
	sa.sa_handler = sigint_handler;
	sa.sa_flags = 0;
	sigemptyset(&sa.sa_mask);
	sigaction(SIGINT, &sa, &old_sa);
#endif

	RCore *core = a->core;
	while (a->running) {
#ifdef _WIN32
		if (WaitForSingleObject(a->cancel_event, 0) == WAIT_OBJECT_0) { cancelled = true; break; }
#else
		if (a->cancelled) { cancelled = true; break; }
#endif

		R2AMsg *m = queue_pop(a, R2A_ASK_TIMEOUT_MS);
		if (!m) {
#ifdef _WIN32
			if (WaitForSingleObject(a->cancel_event, 0) == WAIT_OBJECT_0) cancelled = true;
#else
			if (a->cancelled) cancelled = true;
#endif
			else printf("%s", "\n[r2agent] Timeout waiting for response\n");
			break;
		}

		RJson *json = m->json;

		const RJson *jid = r_json_get(json, "id");
		if (jid && jid->type == R_JSON_INTEGER && jid->num.s_value == req_id) {
			const RJson *jerr = r_json_get(json, "error");
			if (jerr) {
				const RJson *jmsg = r_json_get(jerr, "message");
				if (jmsg && jmsg->str_value) {
					printf("\n[r2agent] Error: %s\n", jmsg->str_value);
				}
			}
			msg_free(m);
			break;
		}

		const RJson *jmethod = r_json_get(json, "method");
		const RJson *jparams = r_json_get(json, "params");

		if (jmethod && jmethod->str_value) {
			const char *method = jmethod->str_value;

			if (!strcmp(method, "stream") && jparams) {
				const RJson *jtype = r_json_get(jparams, "type");
				const RJson *jdata = r_json_get(jparams, "data");

				if (jtype && jtype->str_value) {
					const char *stype = jtype->str_value;

					if (!strcmp(stype, "text_delta") && jdata) {
						const RJson *jdelta = r_json_get(jdata, "delta");
						if (jdelta && jdelta->str_value) {
							printf("%s", jdelta->str_value);
							fflush(stdout);
						}
					} else if (!strcmp(stype, "agent_start") && jdata) {
						const RJson *jname = r_json_get(jdata, "name");
						if (jname && jname->str_value) {
							printf("\n[%s] ", jname->str_value);
							fflush(stdout);
						}
					} else if (!strcmp(stype, "tool_call") && jdata) {
						const RJson *jname = r_json_get(jdata, "name");
						const RJson *jargs = r_json_get(jdata, "args");
						if (jname && jname->str_value) {
							printf("\n  → %s(", jname->str_value);
						}
						if (jargs && jargs->type == R_JSON_OBJECT) {
							bool first = true;
							const RJson *arg;
							for (arg = jargs->children.first; arg; arg = arg->next) {
								if (!first) printf(", ");
								first = false;
								printf("%s=", arg->key ? arg->key : "?");
								switch (arg->type) {
								case R_JSON_STRING:
									printf("%s", arg->str_value);
									break;
								case R_JSON_INTEGER:
									printf("%"PFMT64d, arg->num.s_value);
									break;
								case R_JSON_BOOLEAN:
									printf("%s", arg->num.u_value ? "true" : "false");
									break;
								case R_JSON_DOUBLE:
									printf("%g", arg->num.dbl_value);
									break;
								case R_JSON_NULL:
									printf("null");
									break;
								default:
									printf("...");
									break;
								}
							}
						}
						printf(")\n");
						fflush(stdout);
					}
				}
			} else if (!strcmp(method, "tool_call") && jparams) {
				const RJson *jcall_id = r_json_get(jparams, "id");
				const RJson *jname = r_json_get(jparams, "name");
				const RJson *jargs = r_json_get(jparams, "args");

				const char *call_id = jcall_id && jcall_id->str_value ? jcall_id->str_value : "";
				const char *name = jname && jname->str_value ? jname->str_value : "";

				char *result = NULL;
				if (!strcmp(name, "r2cmd") && jargs) {
					const RJson *jcmd = r_json_get(jargs, "command");
					if (jcmd && jcmd->str_value) {
						r2a_log_reset(a);
						result = r_core_cmd_str(core, jcmd->str_value);
						char *err = r2a_log_drain(a);
						if (err) {
							char *newres = r_str_newf("%s<log>\n%s\n</log>\n",
								result ? result : "", err);
							free(result);
							free(err);
							result = newres;
						}
					}
				} else if (name[0]) {
					result = r_str_newf("Unknown tool: %s", name);
				}
				if (!result) result = strdup("");

				PJ *rpj = pj_new();
				if (rpj) {
					pj_o(rpj);
					pj_ks(rpj, "jsonrpc", "2.0");
					pj_ks(rpj, "method", "tool_result");
					pj_k(rpj, "params");
					pj_o(rpj);
					pj_ks(rpj, "id", call_id);
					pj_ks(rpj, "result", result);
					pj_end(rpj);
					pj_end(rpj);
					char *resp_msg = pj_drain(rpj);
					r2a_send_message(a, resp_msg);
					free(resp_msg);
				}
				free(result);
			}
		}

		msg_free(m);
	}

#ifndef _WIN32
	sigaction(SIGINT, &old_sa, NULL);
	g_current_agent = NULL;
#endif

	a->in_request = false;

	if (cancelled) {
		printf("%s", "\n[interrupted]\n");
	} else {
		printf("%s", "\n");
	}
	fflush(stdout);
}

// Session Management

static R2AMsg *r2a_session_list(R2A *a, bool filter_binary) {
	char params[64];
	snprintf(params, sizeof(params), "{\"filter_binary\":%s}", filter_binary ? "true" : "false");
	return r2a_send_request(a, "session_list", params, R2A_TIMEOUT_MS);
}

static R2AMsg *r2a_session_new(R2A *a) {
	return r2a_send_request(a, "session_new", "{}", R2A_TIMEOUT_MS);
}

static R2AMsg *r2a_session_switch(R2A *a, const char *session_id) {
	PJ *pj = pj_new();
	pj_o(pj);
	pj_ks(pj, "session_id", session_id);
	pj_end(pj);
	char *params = pj_drain(pj);
	R2AMsg *resp = r2a_send_request(a, "session_switch", params, R2A_TIMEOUT_MS);
	free(params);
	return resp;
}

static R2AMsg *r2a_session_delete(R2A *a, const char *session_id) {
	PJ *pj = pj_new();
	pj_o(pj);
	pj_ks(pj, "session_id", session_id);
	pj_end(pj);
	char *params = pj_drain(pj);
	R2AMsg *resp = r2a_send_request(a, "session_delete", params, R2A_TIMEOUT_MS);
	free(params);
	return resp;
}

// Help Text

static void show_help(void) {
	printf("%s",
		"Usage: r2a[vs?] [prompt]  AI-powered analysis assistant\n"
		"| r2a <prompt>        ask the AI a question\n"
		"| r2av                show version info\n"
		"| r2as[?]             session management (see r2as?)\n"
		"Append ? to any command for detailed help. Press Ctrl+C to cancel.\n"
	);
}

static void show_session_help(void) {
	printf("%s",
		"Usage: r2as[*S-?] [session_id]  Session management\n"
		"| r2as                list sessions for current binary\n"
		"| r2as*               list all sessions\n"
		"| r2as <id>           switch to session by id\n"
		"| r2as <n>            switch to session by index (nth)\n"
		"| r2aS                create new session\n"
		"| r2as-[?] <id>       delete session (see r2as-?)\n"
	);
}

static void show_delete_session_help(void) {
	printf("%s",
		"Usage: r2as- <session_id>  Delete a session\n"
		"| r2as- <id>          delete session by id\n"
		"| r2as- <n>           delete session by index (nth)\n"
	);
}

static void show_version_help(void) {
	printf("%s", "| r2av                show plugin and server version\n");
}

// Utility Functions

static bool parse_cmd(const char *cmd, char *opts, const char **args) {
	if (!r_str_startswith(cmd, "r2a")) return false;

	const char *p = cmd + 3;
	int i = 0;
	while (*p && *p != ' ' && i < 15) {
		opts[i++] = *p++;
	}
	opts[i] = '\0';

	while (*p == ' ') p++;
	*args = (*p) ? p : NULL;
	return true;
}

static bool has_opt(const char *opts, char c) {
	return strchr(opts, c) != NULL;
}

static void print_session_list(const RJson *sessions) {
	if (!sessions || sessions->type != R_JSON_ARRAY) return;

	printf("%-4s %-40s %-20s %-20s %s\n", "nth", "session_id", "binary", "last_accessed", "current");
	printf("%s", "───────────────────────────────────────────────────────────────────────────────────────────────\n");

	int idx = 0;
	const RJson *s;
	for (s = sessions->children.first; s; s = s->next, idx++) {
		const RJson *jid = r_json_get(s, "session_id");
		const RJson *jbin = r_json_get(s, "binary_name");
		const RJson *jlast = r_json_get(s, "last_accessed");
		const RJson *jcur = r_json_get(s, "is_current");

		const char *sid = jid && jid->str_value ? jid->str_value : "";
		const char *bin = jbin && jbin->str_value ? jbin->str_value : "";
		const char *last = jlast && jlast->str_value ? jlast->str_value : "";
		bool is_cur = jcur && jcur->type == R_JSON_BOOLEAN && jcur->num.u_value;

		char last_trunc[20] = {0};
		r_str_ncpy(last_trunc, last, sizeof(last_trunc));

		printf("%-4d %-40s %-20s %-20s %s\n", idx, sid, bin, last_trunc, is_cur ? "*" : "");
	}
}

// Plugin Callbacks

static R2A *get_agent(R2AData *data, RCore *core) {
	if (!data->agent) {
		data->agent = r2a_new();
	}
	if (!data->agent) {
		R_LOG_ERROR("Could not allocate agent");
		return NULL;
	}
	if (!r2a_is_running(data->agent)) {
		if (!r2a_start(data->agent, core)) {
			return NULL;
		}
	}
	return data->agent;
}

static bool r2a_init(RCorePluginSession *cps) {
	R2AData *data = R_NEW0(R2AData);
	if (!data) return false;
	cps->data = data;

	char *autostart = r_sys_getenv("R2AGENT_AUTOSTART");
	if (autostart && !strcmp(autostart, "1")) {
		get_agent(data, cps->core);
	}
	free(autostart);

	return true;
}

static bool r2a_fini(RCorePluginSession *cps) {
	R2AData *data = cps->data;
	if (data) {
		if (data->agent) r2a_free(data->agent);
		R_FREE(data);
	}
	cps->data = NULL;
	return true;
}

static bool r2a_call(RCorePluginSession *cps, const char *input) {
	char opts[16] = {0};
	const char *args = NULL;

	if (!parse_cmd(input, opts, &args)) return false;

	RCore *core = cps->core;
	R2AData *data = cps->data;

	if (has_opt(opts, '?')) {
		if (has_opt(opts, 's') && has_opt(opts, '-')) {
			show_delete_session_help();
		} else if (has_opt(opts, 's')) {
			show_session_help();
		} else if (has_opt(opts, 'v')) {
			show_version_help();
		} else {
			show_help();
		}
		return true;
	}

	if (has_opt(opts, 'v')) {
		R2A *agent = get_agent(data, core);
		printf("Plugin version: %s\n", R2A_VERSION);
		if (agent && r2a_is_running(agent)) {
			printf("%s", "Server: running (subprocess)\n");
		} else {
			printf("%s", "Server: not running\n");
		}
		return true;
	}

	if (has_opt(opts, 's') || has_opt(opts, 'S')) {
		R2A *agent = get_agent(data, core);
		if (!agent) {
			printf("%s", "[r2agent] Error: Could not start r2agent subprocess\n");
			return true;
		}

		if (has_opt(opts, 'S')) {
			R2AMsg *resp = r2a_session_new(agent);
			if (resp) {
				const RJson *jresult = r_json_get(resp->json, "result");
				if (jresult) {
					const RJson *jid = r_json_get(jresult, "session_id");
					if (jid && jid->str_value && strlen(jid->str_value) > 0) {
						printf("Created new session: %s\n", jid->str_value);
					}
				}
				msg_free(resp);
			} else {
				printf("%s", "[r2agent] Error: Failed to create session\n");
			}
			return true;
		}

		if (has_opt(opts, '-')) {
			if (!args) {
				show_delete_session_help();
				return true;
			}
			R2AMsg *resp = r2a_session_delete(agent, args);
			if (resp) {
				const RJson *jerr = r_json_get(resp->json, "error");
				if (jerr) {
					const RJson *jmsg = r_json_get(jerr, "message");
					printf("[r2agent] Error: %s\n", jmsg && jmsg->str_value ? jmsg->str_value : "Unknown error");
				} else {
					const RJson *jresult = r_json_get(resp->json, "result");
					const RJson *jid = jresult ? r_json_get(jresult, "session_id") : NULL;
					printf("Deleted session: %s\n", jid && jid->str_value ? jid->str_value : args);
				}
				msg_free(resp);
			} else {
				printf("%s", "[r2agent] Error: Failed to delete session\n");
			}
			return true;
		}

		if (has_opt(opts, '*')) {
			R2AMsg *resp = r2a_session_list(agent, false);
			if (!resp) {
				printf("%s", "[r2agent] Error: Failed to list sessions\n");
				return true;
			}
			const RJson *jresult = r_json_get(resp->json, "result");
			if (!jresult || jresult->type != R_JSON_ARRAY || jresult->children.count == 0) {
				printf("%s", "No sessions found\n");
			} else {
				print_session_list(jresult);
			}
			msg_free(resp);
			return true;
		}

		if (args) {
			R2AMsg *resp = r2a_session_switch(agent, args);
			if (resp) {
				const RJson *jerr = r_json_get(resp->json, "error");
				if (jerr) {
					const RJson *jmsg = r_json_get(jerr, "message");
					printf("[r2agent] Error: %s\n", jmsg && jmsg->str_value ? jmsg->str_value : "Unknown error");
				} else {
					const RJson *jresult = r_json_get(resp->json, "result");
					const RJson *jid = jresult ? r_json_get(jresult, "session_id") : NULL;
					printf("Switched to session: %s\n", jid && jid->str_value ? jid->str_value : args);
				}
				msg_free(resp);
			} else {
				printf("%s", "[r2agent] Error: Failed to switch session\n");
			}
			return true;
		}

		R2AMsg *resp = r2a_session_list(agent, true);
		if (!resp) {
			printf("%s", "[r2agent] Error: Failed to list sessions\n");
			return true;
		}
		const RJson *jresult = r_json_get(resp->json, "result");
		if (!jresult || jresult->type != R_JSON_ARRAY || jresult->children.count == 0) {
			printf("%s", "No sessions for current binary\n");
		} else {
			print_session_list(jresult);
		}
		msg_free(resp);
		return true;
	}

	if (args) {
		R2A *agent = get_agent(data, core);
		if (!agent) {
			printf("%s", "[r2agent] Error: Could not start r2agent subprocess\n");
			return true;
		}
		r2a_ask(agent, args);
		return true;
	}

	show_help();
	return true;
}

// Plugin Registration

RCorePlugin r_core_plugin_r2a = {
	.meta = {
		.name = "r2a",
		.desc = "AI-powered reverse engineering assistant (r2a?)",
		.author = "r2agent",
		.license = "MIT",
	},
	.call = r2a_call,
	.init = r2a_init,
	.fini = r2a_fini,
};

#ifndef R2_PLUGIN_INCORE
R_API RLibStruct radare_plugin = {
	.type = R_LIB_TYPE_CORE,
	.data = &r_core_plugin_r2a,
	.version = R2_VERSION,
	.abiversion = R2_ABIVERSION
};
#endif
