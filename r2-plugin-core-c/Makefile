DOTLIB=.$(shell r2 -NNH R2_LIBEXT)

R2SYSPLUGDIR=$(shell r2 -NNH R2_LIBR_PLUGINS)
R2USRPLUGDIR=$(shell r2 -NNH R2_USER_PLUGINS)

R2_CFLAGS=$(shell pkg-config --cflags r_core)
R2_LDFLAGS=$(shell pkg-config --libs r_core)
ifeq ($(R2_CFLAGS),)
R2_CFLAGS=$(shell r2 -NNH R2_CFLAGS)
R2_LDFLAGS=$(shell r2 -NNH R2_LDFLAGS)
endif

CFLAGS+=$(R2_CFLAGS)
LDFLAGS+=$(R2_LDFLAGS)
CFLAGS+=-fPIC -Wall -g

OBJS=src/core_r2a.o

all: $(OBJS)
	$(CC) -fPIC -shared -o core_r2a$(DOTLIB) $(OBJS) $(CFLAGS) $(LDFLAGS)

user-install: all
	mkdir -p $(R2USRPLUGDIR)
	cp -f core_r2a$(DOTLIB) $(R2USRPLUGDIR)/core_r2a$(DOTLIB)

user-uninstall:
	rm -f $(R2USRPLUGDIR)/core_r2a$(DOTLIB)

install:
	mkdir -p $(R2SYSPLUGDIR)
	cp -f core_r2a$(DOTLIB) $(R2SYSPLUGDIR)/core_r2a$(DOTLIB)

uninstall:
	rm -f $(R2SYSPLUGDIR)/core_r2a$(DOTLIB)

clean:
	rm -f $(OBJS) core_r2a$(DOTLIB)
