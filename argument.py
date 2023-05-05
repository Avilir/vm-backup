#!/usr/bin/env python
"""
This module contain a class for argument parser, check the arguments and
verify the arguments data
"""
# Built-in modules
import argparse
import base64
import logging
import os
import sys

# 3ed party Modules

# Internal modules

logger = logging.getLogger(__name__)
VMB = os.path.basename(__file__)
CFG = f"{VMB.split('.')[0]}.cfg"


class Arguments:
    def __init__(self):
        """
        Initialize the object, add the arguments and pars the CLI
        """
        self.parser = argparse.ArgumentParser(add_help=True)
        self._build()
        self.args = self.args = self.parser.parse_args()

    def _build(self):
        """
        This function define the program arguments in the command line
        """
        # ==================  Help arguments
        self.parser.add_argument(
            "--config",
            action="store_true",
            default=False,
            help="for config-file parameter usage",
        )
        self.parser.add_argument(
            "--example",
            action="store_true",
            default=False,
            help="for additional parameter usage",
        )

        # ==================  Some boolean arguments
        self.parser.add_argument(
            "--preview",
            action="store_true",
            default=False,
            help="preview/validate vm-backup config parameters and xen-server password",
        )
        self.parser.add_argument(
            "--compress",
            action="store_true",
            default=False,
            help="only for vm-export functions automatic compression",
        )
        self.parser.add_argument(
            "--ignore_extra_keys",
            action="store_true",
            default=False,
            help="some config files may have extra params",
        )
        self.parser.add_argument(
            "--pre_clean",
            action="store_true",
            default=False,
            help="delete older backup(s) before performing new backup",
        )
        self.parser.add_argument(
            "-d",
            "--debug",
            action="store_true",
            default=False,
            help="Run in debug mode - adding more output data",
        )
        self.parser.add_argument(
            "-v",
            "--verbose",
            action="store_true",
            default=False,
            help="Run in verbose mode - adding more output data",
        )

        # ==================  Password arguments
        passwords_group = self.parser.add_mutually_exclusive_group(required=False)
        passwords_group.add_argument(
            "-p",
            "--password",
            help="xen server 'root' password",
        )
        passwords_group.add_argument(
            "--password-file",
            action="store",
            default="",
            help="file name to store the obscured 'root' password of the Xen-Server",
        )

        # ==================  Configuration arguments
        self.parser.add_argument(
            "--config-file",
            action="store",
            default="vmbackup.cfg",
            help="A common choice for production crontab execution",
        )
        self.parser.add_argument(
            "--section",
            action="store",
            default="default",
            help="Which section from the configuration file to use",
        )
        self.parser.add_argument(
            "--vm-export",
            action="append",
            default=[],
            help="a single vm name or a vm regular expression that defaults to vm-export",
        )

    def help_check(self):
        """
        Function to check if a Help argument was passed, and display the
        appropriate help screen and exit the program.

        """
        if self.args.config or self.args.example:
            if self.args.config:
                usage_config_file()
            if self.args.example:
                usage_examples()
            sys.exit(0)

    def get_password(self, pass_file=""):
        """
        Function that return the XEN server password, from the CLI or from a file.
        In the file, the password is encoded.
        If it needs to get the password from a file and the file doesn't exist, it
        exits the program with an error message.

        Return:
            str : the decoded password for the XEN server
        """
        if self.args.password is not None:
            return self.args.password
        if self.args.password_file == "":
            self.args.password_file = pass_file
        # At this point, we must read the password from the file
        if os.path.exists(self.args.password_file):
            with open(self.args.password_file, "rb") as fh:
                data = fh.read()
                password = base64.b64decode(data).decode("UTF-8")
            return password
        else:
            raise (f"Error: password file ({self.args.password_file}) doesn't exist !")
            # self.parser.print_help()
            # exit(1)

    def is_preview(self):
        return self.args.preview

    def is_compress(self):
        return self.args.compress

    def is_ignore_extra_keys(self):
        return self.args.ignore_extra_keys

    def is_pre_clean(self):
        return self.args.pre_clean


def usage_config_file():
    print("Usage-config-file:")
    with open(CFG, "r") as f:
        print(f.read())


def usage_examples():
    print(
        f"""
    Usage-examples:

      # config file
      ./{VMB} -p|--password password --config-file weekend.cfg

      # single VM name, which is case sensitive
      ./{VMB} -p|--password password --vm-selector DEV-mySql

      # single VM name using vdi-export instead of vm-export
      ./{VMB} -p|--password password --vm-selector vdi-export=DEV-mySql

      # single VM name with spaces in name
      ./{VMB} -p|--password password --vm-selector "DEV mySql"

      # VM regular expression - which may be more than one VM
      ./{VMB} -p|--password password --vm-selector DEV-my.*

      # all VMs in pool
      ./{VMB} -p|--password password --vm-selector ".*"

      # use password file + config file
      ./{VMB} --password-file /root/VmBackup.pass --config-file monthly.cfg
    """
    )


if __name__ == "__main__":
    args = Arguments()
    args.help_check()
    print(f"The password is {args.get_password()}")
