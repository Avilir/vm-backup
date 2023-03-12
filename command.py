#!/usr/bin/env python

# Built-in modules
import json
import logging
import os.path
import subprocess

# 3ed party modules

# Local modules
import constnts


logger = logging.getLogger(__name__)


class Command:
    def __init__(self, host="localhost", user="root"):
        self._host = host
        self._user = user
        if host != "localhost":
            self._rcmd = f"ssh {self.user()}@{self.host()}"
        else:
            self._rcmd = ""

    def host(self, value=""):
        if value != "":
            self._host = value
            if value != "localhost":
                self._rcmd = f"ssh {self.user()}@{value}"
        return self._host

    def user(self, value=""):
        if value != "":
            self._user = value
            self._rcmd = f"ssh {value}@{self.host()}"
        return self._user

    def run(self, cmd, timeout=600, out_format="string", **kwargs):
        """
        Running command on the OS and return the STDOUT & STDERR outputs
        in case of argument is not string or list, return error message

        Args:
            cmd (str/list): the command to execute
                Note: if the command contain quoted argument(s), use the list format of the command
                and not the one string format
            timeout (int): the command timeout in seconds, default is 10 Min.
            out_format (str): in which format to return the output: string / list / json / last
            kwargs (dict): dictionary of argument as subprocess get

        Returns:
            list or str : all STDOUT and STDERR output as list of lines, or one string separated by NewLine

        """

        ERROR = "Error in command"
        if isinstance(cmd, str):
            command = cmd.split()
        elif isinstance(cmd, list):
            command = cmd
        else:
            return ERROR

        for key in ["stdout", "stderr", "stdin"]:
            kwargs[key] = subprocess.PIPE

        if "out_format" in kwargs:
            out_format = kwargs["out_format"]
            del kwargs["out_format"]

        logger.info(f"Going to format output as {out_format}")
        logger.info(f"Going to run {cmd} with timeout of {timeout}")
        try:
            cp = subprocess.run(command, timeout=timeout, **kwargs)
            output = cp.stdout.decode().strip()
            err = cp.stderr.decode().strip()
            # exit code is not zero
            if cp.returncode:
                logger.error(f"Command finished with non zero exitcode ({cp.returncode}): {err}")
                output += f"\n{ERROR} ({cp.returncode}): {err}"
        except Exception as ex:
            logger.error(f"The command didn't ran: {ex}")
            output = f"Error in command: {ex}"

        if out_format == "rc":
            return cp.returncode

        if out_format in ["list", "last"]:
            output = output.split("\n")  # convert output to list
            if output[-1] == "":
                output.pop()  # remove last empty element from the list

        if out_format == "last":
            try:
                output = output[-1] if len(output) > 1 else output[0]
            except IndexError:
                output = ""

        if out_format == "json":
            try:
                output = json.loads(output)
            except Exception as ex:
                logger.error(f"{ERROR} : Can not format output as JSON : {ex}")

        return output

    def run_remote(self, cmd, timeout=600, out_format="string", **kwargs):
        command = f"{self._rcmd} {cmd}"
        return self.run(command, timeout=timeout, out_format=out_format, **kwargs)

    def run_xe(self, cmd, out_format="last"):
        command = f'{os.path.join(constnts.xe_path, "xe")} {cmd}'
        return self.run_remote(command, out_format=out_format)

    def run_df(self, log_msg, path):
        logger.info(log_msg)
        results = self.run(f"df -Th {path}", out_format="string")
        logger.info(results)

    def check_if_vm_is_running(self, vm_name):
        # is vm currently running?
        cmd = f'vm-list name-label="{vm_name}" params=power-state'
        results = self.run_xe(cmd, out_format="last")
        return True if "running" in results else False

    def destroy_vdi_snapshot(self, snapshot_uuid, log_prefix="cmd"):
        cmd = f"vdi-destroy uuid={snapshot_uuid}"
        logger.info(f"{log_prefix}: xe {cmd}")
        if self.run_xe(cmd, out_format="rc") != 0:
            logger.warning(f"xe {cmd}")
            return "warning"
        else:
            return "success"

    def dir_list(self, path):
        results = self.run_remote(f"ls {path}", out_format="list")
        if len(results) > 1:
            results.sort()
        return results

    def path_exists(self, path):
        command = f"/usr/bin/touch {path}"
        return self.run_remote(command, out_format='rc')

    def path_delete(self, path):
        command = f"rm -rf {path}"
        return self.run_remote(command, out_format='rc')

    def write_to_file(self, filename, data):
        for line in data:
            command = f"echo '{line}' >> {filename}"
            if self.run_remote(command, out_format="rc") != 0:
                logger.debug(f"cannot write to the file : {logger.debug}")


if __name__ == "__main__":
    c = Command()
    print(c.run("ls -l", out_format="list"))
    print(c.run("ls -l", out_format="string"))
    print(c.run("kuku", out_format="string"))
    print(c.run("python main.py", out_format="list"))
