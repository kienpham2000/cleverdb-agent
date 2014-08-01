#!/usr/bin/python
import subprocess
from time import sleep
import os
import sys
import logging
import logging.handlers
import base64
import json
import signal
import optparse
import pwd
import tempfile
import shutil
import hashlib

# Python2 vs Python3 black magic
py_version = sys.version_info[:3]
# Python 2
if py_version < (3, 0, 0):
    import urllib2 as urllib
    from ConfigParser import ConfigParser
    from ConfigParser import Error as ConfigParserError

    chr = unichr
    native_string = str
    decode_string = unicode
    encode_string = str
    unicode_string = unicode
    string_type = basestring
    byte_string = str
else:
    import urllib.request as urllib
    from configparser import ConfigParser
    from configparser import Error as ConfigParserError
    import builtins

    byte_string = bytes
    string_type = str
    native_string = str
    decode_string = bytes.decode
    encode_string = lambda s: bytes(s, 'utf-8')
    unicode_string = str
# end of magic block

__version__ = '0.2.2'
logging.QUIET = 1000
logger = logging.getLogger("cleverdb-agent")

prog = None
temps = None


def signal_handler(signo, frame):
    global prog, temps
    logger.info("Got signqal %s, terminating", signo)
    if prog:
        logger.info("Stopping SSH client")
        prog.send_signal(signal.SIGTERM)
    if temps:
        shutil.rmtree(temps)
    sys.exit(0)


signal.signal(signal.SIGTERM, signal_handler)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGQUIT, signal_handler)


class OptionParser(optparse.OptionParser):
    usage = '%prog'
    LOG_LEVELS = {
        'all': logging.NOTSET,
        'debug': logging.DEBUG,
        'error': logging.ERROR,
        'critical': logging.CRITICAL,
        'info': logging.INFO,
        'warning': logging.WARNING,
        'quiet': logging.QUIET,
    }

    # Make a list of log level names sorted by log level
    SORTED_LEVEL_NAMES = [
        l[0] for l in sorted(LOG_LEVELS.items(), key=lambda x: x[1])
    ]

    def __init__(self):
        optparse.OptionParser.__init__(self, version=__version__)
        self.add_option(
            '-l', '--log-level',
            choices=list(self.LOG_LEVELS),
            dest='log_level',
            default='info',
            help='logging log level. One of {0}. '
                 'Default: \'{1}\'.'.format(
                ', '.join([repr(l) for l in self.SORTED_LEVEL_NAMES]),
                'info')
        )
        self.add_option(
            '--config',
            action='store',
            dest='config',
            default='/etc/cleverdb-agent/config'
        )
        self.add_option(
            '--rsync',
            action='store_true',
            default=False,
            dest='rsync'
        )

def run(uploader, host, db_id, api_key, filename):
    retry_count = 0

    global prog, temps

    while True:
        # get config from api:
        config = _get_config(host, db_id, api_key)

        # save private key to disk
        temps = tempfile.mkdtemp()
        key = tempfile.NamedTemporaryFile(dir=temps, delete=False)
        os.chmod(key.name, int("400", 8))
        key.write(config['ssh_private_key'])
        key.close()

        try:
            uploader(config, key.name, filename,
                     "/uploads/{}.sql".format(db_id))
            checksum_file = tempfile.NamedTemporaryFile(dir=temps, delete=False)
            checksum_file.write("sha1:{}\n".format(checksum(filename)))
            checksum_file.close()
            uploader(config, key.name, checksum_file.name,
                     "/uploads/{}.checksum".format(db_id))
        except Exception as e:
            logger.debug(e)
        else:
            return
        finally:
            shutil.rmtree(temps)
            temps = None

        # ssh tunnel broken at this point
        # retry getting latest config from api:
        retry_count += 1
        logger.info("Sleeping...%i seconds" % retry_count)
        sleep(retry_count)
        logger.info("Retrying upload file...%i" % retry_count)


def scp_cp(config, keyfile, src, dst):
    scp_commandline = [
        "scp",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "StrictHostKeyChecking=no",
        "-i", keyfile,
        "-P", byte_string(config['port']),
        src,
        byte_string("{}@{}:{}".format(config['user'], config['ip'], dst))
    ]
    logger.debug(scp_commandline)
    check_call(scp_commandline)

def rsync_cp(config, keyfile, src, dst):
    # TODO: generate 2 key pair so we don't have to ignore host checking
    ssh_commandline = [
        "ssh",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "StrictHostKeyChecking=no",
        "-i", keyfile,
        "-p", byte_string(config['port'])
    ]

    rsync_commandline = [
        "rsync",
        "-e", " ".join(ssh_commandline),
        src,
        byte_string("%s@%s:%s" % (config['user'], config['ip'], dst)),
    ]
    logger.info("Starting RSYNC...")
    logger.debug("RSYNC option is ")
    logger.debug(rsync_commandline)
    check_call(rsync_commandline)

def check_call(*args, **kwargs):
    """Modified version of subprocess.check_call """
    try:
        prog = subprocess.Popen(*args, **kwargs)
        prog.communicate()
    finally:
        if prog.returncode == 0:
            logger.info("All OK")
        else:
            logger.error(
                "command terminated with non-zero error code: {}".format(
                    prog.returncode
                )
            )
            cmd = kwargs.get("args") or args[0]
            raise subprocess.CalledProcessError(prog.returncode, cmd)
        prog = None

def checksum(filename):
    sha1 = hashlib.sha1()
    with open(filename, 'r') as f:
        while True:
            data = f.read(65535)
            if len(data) == 0:
                break
            sha1.update(data)
        return sha1.hexdigest()

def _get_config(host, db_id, api_key):
    # TODO: remove basic auth
    auth = base64.encodestring(
        byte_string('{}:{}'.format('cleverdb', 'c13VRvDblc')))[:-1]
    url = '%s/v1/agent/%s/configuration?api_key=%s' % (
        host, db_id, api_key)
    req = urllib.Request(url)
    req.add_header("Authorization", "Basic %s" % auth)
    logger.debug(url)

    retry_count = 0
    while True:
        try:
            handle = urllib.urlopen(req)
            res = json.loads(handle.read())
            logger.debug(res)
            return res
        except Exception as e:
            retry_count += 1
            logger.info("Retrying to connect to api...")
            logger.info("Sleeping...%i seconds" % retry_count)
            sleep(retry_count)
            logger.error(e)


def setup_logging(log_level):
    logging.basicConfig(level=log_level)
    logger.info("Logger initialized")


def main():
    option_parser = OptionParser()
    (options, args) = option_parser.parse_args()
    if len(args) != 1:
        sys.stderr.write("Missing filename\n")
        sys.exit(1)

    setup_logging(
        option_parser.LOG_LEVELS[options.log_level],
    )

    # check if config file exist and readble:
    if (not os.path.isfile(options.config) or
            not os.access(options.config, os.R_OK)):
        logger.critical("Configuration file {0} does not exist or "
                        "not readable".format(options.config))
        sys.exit(1)

    try:
        cp = ConfigParser()
        cp.read(options.config)
        db_id = cp.get('agent', 'db_id')
        api_key = cp.get('agent', 'api_key')
        host = cp.get('agent', 'connect_host')
    except ConfigParserError as e:
        logger.critical("Error parsing configuration file {0}: {1}".format(
            options.config,
            e.message
        ))
        sys.exit(1)

    uploader = options.rsync and rsync_cp or scp_cp
    run(uploader, host, db_id, api_key, args[0])


if __name__ == '__main__':
    main()