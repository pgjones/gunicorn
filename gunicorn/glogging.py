# -*- coding: utf-8 -
#
# This file is part of gunicorn released under the MIT license.
# See the NOTICE for more information.

import datetime
import logging
logging.Logger.manager.emittedNoHandlerWarning = 1
import os
import sys
import traceback
import threading

try:
    from logging.config import fileConfig
except ImportError:
    from gunicorn.logging_config import fileConfig

from gunicorn import util

class LazyWriter(object):

    """
    File-like object that opens a file lazily when it is first written
    to.
    """

    def __init__(self, filename, mode='w'):
        self.filename = filename
        self.fileobj = None
        self.lock = threading.Lock()
        self.mode = mode

    def open(self):
        if self.fileobj is None:
            self.lock.acquire()
            try:
                if self.fileobj is None:
                    self.fileobj = open(self.filename, self.mode)
            finally:
                self.lock.release()
        return self.fileobj

    def write(self, text):
        fileobj = self.open()
        fileobj.write(text)
        fileobj.flush()

    def writelines(self, text):
        fileobj = self.open()
        fileobj.writelines(text)
        fileobj.flush()

    def flush(self):
        self.open().flush()

class Logger(object):

    LOG_LEVELS = {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
        "debug": logging.DEBUG
    }

    error_fmt = r"%(asctime)s [%(process)d] [%(levelname)s] %(message)s"
    datefmt = r"%Y-%m-%d %H:%M:%S"

    access_fmt = "%(message)s"

    def __init__(self, cfg):
        self.error_log = logging.getLogger("gunicorn.error")
        self.access_log = logging.getLogger("gunicorn.access")
        self.error_handlers = []
        self.access_handlers = []
        self.cfg = cfg
        self.setup(cfg)

    def setup(self, cfg):
        if not cfg.logconfig:
            loglevel = self.LOG_LEVELS.get(cfg.loglevel.lower(), logging.INFO)
            self.error_log.setLevel(loglevel)
            self.access_log.setLevel(logging.INFO)


            if cfg.errorlog != "-":
                # if an error log file is set redirect stdout & stderr to
                # this log file.
                stdout_log = LazyWriter(cfg.errorlog, 'a')
                sys.stdout = stdout_log
                sys.stderr = stdout_log

            # set gunicorn.error handler
            self._set_handler(self.error_log, cfg.errorlog,
                    logging.Formatter(self.error_fmt, self.datefmt))

            # set gunicorn.access handler
            if cfg.accesslog is not None:
                self._set_handler(self.access_log, cfg.accesslog,
                    fmt=logging.Formatter(self.access_fmt))
        else:
            if os.path.exists(cfg.logconfig):
                fileConfig(cfg.logconfig)
            else:
                raise RuntimeError("Error: log config '%s' not found" % path)


    def critical(self, msg, *args, **kwargs):
        self.error_log.critical(msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self.error_log.error(msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self.error_log.warning(msg, *args, **kwargs)

    def info(self, msg, *args, **kwargs):
        self.error_log.info(msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self.error_log.debug(msg, *args, **kwargs)

    def exception(self, msg, *args):
        self.error_log.exception(msg, *args)

    def log(self, lvl, msg, *args, **kwargs):
        if isinstance(lvl, basestring):
            lvl = self.LOG_LEVELS.get(lvl.lower(), logging.INFO)
        self.error_log.log(lvl, msg, *args, **kwargs)

    def access(self, resp, environ, request_time):
        """ Seee http://httpd.apache.org/docs/2.0/logs.html#combined
        for format details
        """

        if not self.cfg.accesslog and not self.cfg.logconfig:
            return

        status = resp.status.split(None, 1)[0]
        atoms = {
                'h': environ['REMOTE_ADDR'],
                'l': '-',
                'u': '-', # would be cool to get username from basic auth header
                't': self.now(),
                'r': "%s %s %s" % (environ['REQUEST_METHOD'],
                    environ['RAW_URI'], environ["SERVER_PROTOCOL"]),
                's': status,
                'b': str(resp.response_length) or '-',
                'f': environ.get('HTTP_REFERER', '-'),
                'a': environ.get('HTTP_USER_AGENT', '-'),
                'T': str(request_time.seconds),
                'D': str(request_time.microseconds)
                }

        # add WSGI request headers
        atoms.update(dict([(k,v) for k, v in environ.items() \
                if k.startswith('HTTP_')]))

        for k, v in atoms.items():
            atoms[k] = v.replace('"', '\\"')

        try:
            self.access_log.info(self.cfg.access_log_format % atoms)
        except:
            self.error(traceback.format_exc())

    def now(self):
        """ return date in Apache Common Log Format """
        now = datetime.datetime.now()
        month = util.monthname[now.month]
        return '[%02d/%s/%04d:%02d:%02d:%02d]' % (now.day, month,
                now.year, now.hour, now.minute, now.second)


    def reopen_files(self):
        for log in (self.error_log, self.access_log):
            for handler in log.handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.acquire()
                    handler.stream.close()
                    handler.stream = open(handler.baseFilename,
                            handler.mode)
                    handler.release()

    def close_on_exec(self):
        for log in (self.error_log, self.access_log):
            for handler in log.handlers:
                if isinstance(handler, logging.FileHandler):
                    handler.acquire()
                    util.close_on_exec(handler.stream.fileno())
                    handler.release()


    def _get_gunicorn_handler(self, log):
        for h in log.handlers:
            if getattr(h, "_gunicorn", False) == True:
                return h

    def _set_handler(self, log, output, fmt):
        # remove previous gunicorn log handler
        h = self._get_gunicorn_handler(log)
        if h:
            log.handlers.remove(h)

        if output == "-":
            h = logging.StreamHandler()
        else:
            util.check_is_writeable(output)
            h = logging.FileHandler(output)

        h.setFormatter(fmt)
        h._gunicorn = True
        log.addHandler(h)
