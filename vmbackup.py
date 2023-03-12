#!/usr/bin/env python

# Builtin modules
import configparser
import datetime
from email.mime.text import MIMEText
import logging
import re
import smtplib
import socket
import sys
import time

# 3ed Party modules
import XenAPI

# Local modules
import argument
from command import Command
from constnts import *

# ############################ HARD CODED DEFAULTS ##########################
# Some global constants
BASE_NAME = "vmbackup"
LOG_FILE = f"{BASE_NAME}.log"
CFG_FILE = f"{BASE_NAME}.cfg"

# Some global variables
section = ""  # the section name to use in the configuration file
all_vms = list()  # list of all VMs in the Xen-Server
vdi_export = list()  # list of vm for the vdi-export operation
backup_vms = list()  # list of vms to back up
message = ""
cmd = Command()

vm_uuid = ""
xvda_uuid = ""
xvda_name_label = ""

# Setting up and reading the configuration file
# Note: all default variables should be in this file and also optionally some
# operation specific variables
config = configparser.ConfigParser()
config.read(CFG_FILE)

# Setting up the log file
logger = logging.getLogger()
logging.basicConfig(
    format="[%(asctime)s] [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
    filename=LOG_FILE,
)


def no_verbose(msg='', level=logging.INFO):
    logger.log(level=level, msg=msg)


def verbose(msg=("=" * 78), level=logging.INFO):
    """
    Log a message to the log file and to the screen, if running in verbose mode,
    otherwise will do nothing.

    Args:
        msg (str): the message to display
        level (int): the log level for the logging
    """

    print(msg, flush=True)
    logger.log(level=level, msg=msg)


def title(msg):
    length = int((76 - len(msg)) / 2)
    ast = "*" * length
    verbose(f"{ast} {msg} {ast}")


def no_debug(msg):
    logger.debug(msg=msg)


def debug(msg):
    """
    Log a message to the log file and to the screen, if running in debug mode,
    otherwise will do nothing.

    Args:
        msg (str): the message to display
    """
    print(f" [DEBUG] {msg}", flush=True)
    logger.debug(msg=msg)


def select_vms(source_list):
    global all_vms
    tmp_list = list()
    for inc_vm in source_list:
        for svm in all_vms:
            new_value, found_match = check_vm_in_server(inc_vm, svm)
            if found_match:
                tmp_list.append(new_value)
                verbose(f"The vm {new_value} Exist !")
        return tmp_list


def main(session):
    success_cnt = 0
    warning_cnt = 0
    error_cnt = 0

    server_name = os.uname()[1].split(".")[0]

    status_log_begin(server_name)

    verbose()
    verbose(f"{BASE_NAME} running on {server_name} ...")
    verbose()
    verbose(
        f"Check if backup directory {config.get(section, 'backup_dir', fallback=DEFAULT_BACKUP_DIR)} is writable ..."
    )
    touchfile = os.path.join(
        config.get(section, "backup_dir", fallback=DEFAULT_BACKUP_DIR),
        "00VMbackupWriteTest",
    )

    verbose(f"Try to create {touchfile}")
    if not cmd.path_exists(touchfile) == 0:
        verbose(
            "Failed to write to backup directory area - FATAL ERROR",
            level=logging.ERROR,
        )
        sys.exit(1)

    else:
        cmd.path_delete(touchfile)
        verbose("Success: backup directory area is writable")

    verbose()
    df_snapshots(f"Space before backups")

    if int(config.get(section, "pool_db_backup", fallback=DEFAULT_POOL_DB_BACKUP)):
        verbose("*** begin backup_pool_metadata ***")
        if not backup_pool_metadata(server_name):
            error_cnt += 1

    # Iterate through all vdi-export= in cfg
    title("vdi-export=")
    for vm_parm in vdi_export:
        verbose(f"*** vdi-export begin {vm_parm} ***")
        beginTime = datetime.datetime.now()
        this_status = "success"

        # get values from vdi-export=
        vm_name = get_vm_name(vm_parm)
        vm_max_backups = get_vm_max_backups(vm_parm)
        verbose(f"vdi-export - vm_name: {vm_name} max_backups: {vm_max_backups}")

        status_log_vdi_export_begin(server_name, vm_name)

        # verify vm_name exists with only one instance for this name returns error-message or vm_object if success
        vm_object = verify_vm_name(vm_name)
        if "ERROR" in vm_object:
            verbose(f"verify_vm_name: {vm_object}")
            status_log_vdi_export_end(server_name, f"ERROR verify_vm_name {vm_name}")
            error_cnt += 1
            # next vm
            continue

        vm_backup_dir = os.path.join(config.get(section, "backup_dir", fallback=DEFAULT_BACKUP_DIR), vm_name)
        # cleanup any old unsuccessful backups and create new full_backup_dir
        full_backup_dir = process_backup_dir(vm_backup_dir)
        # gather_vm_meta produces status: empty or warning-message
        #   and globals: vm_uuid, xvda_uuid, xvda_uuid
        #   => now only need: vm_uuid
        #   since all VM metadta go into an XML file
        vm_meta_status = gather_vm_meta(vm_object, full_backup_dir)
        debug(f"The VM meta status is : {vm_meta_status}")
        if vm_meta_status != "":
            verbose(f"Couldn't gather vm meta: {vm_meta_status}", level=logging.WARNING)
            this_status = "warning"
            # non-fatal - finsh processing for this vm

        debug(f"The VXDA UUID is : {xvda_uuid}")
        # vdi-export only uses xvda_uuid, xvda_uuid
        if xvda_uuid == "":
            verbose("gather_vm_meta has no xvda-uuid", level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"ERROR xvda-uuid not found {vm_name}")
            error_cnt += 1
            # next vm
            continue
        debug(f"The VXDA name is : {xvda_name_label}")
        if xvda_name_label == "":
            verbose("gather_vm_meta has no xvda-name-label", level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"ERROR xvda-name-label not found {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # -----------------------------------------
        # --- begin vdi-export command sequence ---
        verbose("*** vdi-export begin xe command sequence")
        # is vm currently running?
        if cmd.check_if_vm_is_running(vm_name):
            verbose(f"The vm {vm_name} is running")
        else:
            verbose(f"The vm {vm_name }is NOT running")

        # list the vdi we will back up
        command = f"vdi-list uuid={xvda_uuid}"
        verbose(f"1.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") != 0:
            verbose(f"ERROR {command}", level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"VDI-LIST-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # check for old vdi-snapshot for this xvda
        snap_vdi_name_label = f"SNAP_{vm_name}_{xvda_name_label}"
        # replace all spaces with '-'
        snap_vdi_name_label = re.sub(r" ", r"-", snap_vdi_name_label)
        verbose(f"check for prev-vdi-snapshot: {snap_vdi_name_label}")
        command = f"vdi-list name-label='{snap_vdi_name_label}' params=uuid"
        results = cmd.run_xe(command, out_format="last").split()
        debug(f"List of all snaps is : {results}")
        old_snap_vdi_uuid = results[-1] if len(results) >= 1 else ""
        debug(f"The old snapshots is : {old_snap_vdi_uuid}")
        if old_snap_vdi_uuid != "":
            verbose(f"cleanup old-snap-vdi-uuid: {old_snap_vdi_uuid}")
            # vdi-destroy old vdi-snapshot
            if cmd.destroy_vdi_snapshot(old_snap_vdi_uuid) != "success":
                verbose(f"Failed to run {command}", level=logging.WARNING)
                this_status = "warning"
                # non-fatal - finish processing for this vm

        # === pre_cleanup code goes in here ===
        debug(f"Pre clean mode is {pre_clean}")
        if pre_clean:
            pre_cleanup(vm_backup_dir, vm_max_backups)

        # take a vdi-snapshot of this vm
        command = f"vdi-snapshot uuid={xvda_uuid}"
        verbose(f"2.cmd: {command}")
        snap_vdi_uuid = cmd.run_xe(command, out_format="last")
        verbose(f"snap-uuid: {snap_vdi_uuid}")
        if snap_vdi_uuid == "":
            verbose(command, level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"VDI-SNAPSHOT-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # change vdi-snapshot to unique name-label for easy id and cleanup
        command = f'vdi-param-set uuid={snap_vdi_uuid} name-label="{snap_vdi_name_label}"'
        verbose(f"3.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") != 0:
            verbose(command, level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"VDI-PARAM-SET-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # actual-backup: vdi-export vdi-snapshot
        command = "vdi-export "
        command += f"format={config.get(section, 'vdi_export_format', fallback=DEFAULT_VDI_EXPORT_FORMAT)} "
        command += f"uuid={snap_vdi_uuid} "
        full_path_backup_file = os.path.join(
            full_backup_dir,
            f"{vm_name}.{config.get(section, 'vdi_export_format', fallback=DEFAULT_VDI_EXPORT_FORMAT)}",
        )
        command += f'filename="{full_path_backup_file}"'
        verbose(f"4.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") == 0:
            verbose("vdi-export success")
        else:
            verbose(f"{command} Failed to run", level=logging.ERROR)
            status_log_vdi_export_end(server_name, f"VDI-EXPORT-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # cleanup: vdi-destroy vdi-snapshot
        if cmd.destroy_vdi_snapshot(snap_vdi_uuid, log_prefix="5.cmd") != "success":
            verbose(f"{command} Failed to run", level=logging.WARNING)
            this_status = "warning"
            # non-fatal - finsh processing for this vm

        title("vdi-export end")
        # --- end vdi-export command sequence ---
        # ---------------------------------------

        elapseTime = datetime.datetime.now() - beginTime
        backup_file_size = cmd.run_remote(f"du -m {full_path_backup_file}", out_format="last").split()[0]
        backup_file_size = int(int(backup_file_size) / 1024)
        debug(f"The size of the backup file is : [{str(backup_file_size)}GB]")
        final_cleanup(
            full_path_backup_file,
            backup_file_size,
            full_backup_dir,
            vm_backup_dir,
            vm_max_backups,
        )

        if not check_all_backups_success(vm_backup_dir):
            verbose(
                "Cleanup needed - not all backup history is successful",
                level=logging.WARNING,
            )
            this_status = "warning"

        backup_time = f":{str(elapseTime.seconds / 60):.3} Minute"

        if this_status == "success":
            success_cnt += 1
            verbose(f"{BASE_NAME} vdi-export {vm_name} - ***Success*** t{backup_time}")
            status_log_vdi_export_end(
                server_name,
                f"SUCCESS {vm_name},elapse:{backup_time} ; size:{backup_file_size}G",
            )

        elif this_status == "warning":
            warning_cnt += 1
            verbose(f"{BASE_NAME} vdi-export {vm_name} - ***WARNING*** t:{backup_time}")
            status_log_vdi_export_end(
                server_name, f"WARNING {vm_name},elapse:{backup_time} ; size:{backup_file_size}G"
            )

        else:
            # this should never occur since all errors do a continued on to the next vm_name
            error_cnt += 1
            verbose(f"{BASE_NAME} vdi-export {vm_name} - +++ERROR-INTERNAL+++ t:{backup_time}")
            status_log_vdi_export_end(
                server_name,
                f"ERROR-INTERNAL {vm_name},elapse:{backup_time} ; size:{backup_file_size}G",
            )

    # end of for vm_parm in config['vdi-export']:
    ######################################################################
    # Iterate through all vm-export= in cfg
    title(msg="vm-export=")
    for vm_parm in backup_vms:
        verbose(f"*** vm-export begin {vm_parm}")
        beginTime = datetime.datetime.now()
        this_status = "success"

        # get values from vdi-export=
        vm_name = get_vm_name(vm_parm)
        vm_max_backups = get_vm_max_backups(vm_parm)
        verbose(f"vm-export - vm_name: {vm_name} max_backups: {vm_max_backups}")

        status_log_vm_export_begin(server_name, vm_name)

        vm_object = verify_vm_name(vm_name)
        if "ERROR" in vm_object:
            verbose(f"verify_vm_name: {vm_object}")
            status_log_vm_export_end(server_name, f"ERROR verify_vm_name {vm_name}")
            error_cnt += 1
            # next vm
            continue

        vm_backup_dir = os.path.join(config.get(section, "backup_dir", fallback=DEFAULT_BACKUP_DIR), vm_name)
        # cleanup any old unsuccessful backups and create new full_backup_dir
        full_backup_dir = process_backup_dir(vm_backup_dir)

        # gather_vm_meta produces status: empty or warning-message
        #   and globals: vm_uuid, xvda_uuid, xvda_uuid
        vm_meta_status = gather_vm_meta(vm_object, full_backup_dir)
        if vm_meta_status != "":
            verbose(f"gather_vm_meta: {vm_meta_status}", level=logging.WARNING)
            this_status = "warning"
            # non-fatal - finsh processing for this vm
        # vm-export only uses vm_uuid
        if vm_uuid == "":
            verbose("gather_vm_meta has no vm-uuid", level=logging.ERROR)
            status_log_vm_export_end(server_name, f"ERROR vm-uuid not found {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # ----------------------------------------
        # --- begin vm-export command sequence ---
        verbose("*** vm-export begin xe command sequence")
        # is vm currently running?
        if cmd.check_if_vm_is_running(vm_name):
            verbose("vm is running")
        else:
            verbose("vm is NOT running")

        # check for old vm-snapshot for this vm
        snap_name = f"RESTORE_{vm_name}"
        verbose(f"check for prev-vm-snapshot: {snap_name}")
        command = f"vm-list name-label='{snap_name}' params=uuid"  # | /bin/awk -F': ' '{print $2}' | /bin/grep '-'"
        old_snap_vm_uuid = cmd.run_xe(command, out_format="last")
        if old_snap_vm_uuid != "":
            verbose(f"cleanup old-snap-vm-uuid: {old_snap_vm_uuid}")
            # vm-uninstall old vm-snapshot
            command = f"vm-uninstall uuid={old_snap_vm_uuid} force=true"
            verbose(f"cmd: {command}")
            if cmd.run_xe(command, out_format="rc") != 0:
                verbose(command, level=logging.WARNING)
                this_status = "warning"
                status_log_vm_export_end(server_name, f"VM-UNINSTALL-FAIL-1 {vm_name}")
                # non-fatal - finsh processing for this vm

        # === pre_cleanup code goes in here ===
        debug(f"vm_backup_dir: {vm_backup_dir} ; vm_max_backups: {vm_max_backups}")
        if pre_clean:
            pre_cleanup(vm_backup_dir, vm_max_backups)

        # take a vm-snapshot of this vm
        command = f'vm-snapshot vm={vm_uuid} new-name-label="{snap_name}"'
        verbose(f"1.cmd: {command}")
        snap_vm_uuid = cmd.run_xe(command, out_format="last")
        verbose(f"snap-uuid: {snap_vm_uuid}")
        if snap_vm_uuid == "":
            verbose(command, level=logging.ERROR)
            status_log_vm_export_end(server_name, f"SNAPSHOT-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # change vm-snapshot so that it can be referenced by vm-export
        command = f"template-param-set is-a-template=false ha-always-run=false uuid={snap_vm_uuid}"
        verbose(f"2.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") != 0:
            verbose(command, level=logging.ERROR)
            status_log_vm_export_end(server_name, f"TEMPLATE-PARAM-SET-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # vm-export vm-snapshot
        command = f"vm-export uuid={snap_vm_uuid}"
        if compress:
            full_path_backup_file = os.path.join(full_backup_dir, vm_name + ".xva.gz")
            command = f'{command} filename="{full_path_backup_file}" compress=true'
        else:
            full_path_backup_file = os.path.join(full_backup_dir, vm_name + ".xva")
            command = f'{command} filename="{full_path_backup_file}"'
        verbose(f"3.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") == 0:
            verbose("vm-export success")
        else:
            verbose(command, level=logging.ERROR)
            status_log_vm_export_end(server_name, f"VM-EXPORT-FAIL {vm_name}")
            error_cnt += 1
            # next vm
            continue

        # vm-uninstall vm-snapshot
        command = f"vm-uninstall uuid={snap_vm_uuid} force=true"
        verbose(f"4.cmd: {command}")
        if cmd.run_xe(command, out_format="rc") != 0:
            verbose(command, level=logging.WARNING)
            this_status = "warning"
            # non-fatal - finsh processing for this vm

        title("vm-export end")
        # --- end vm-export command sequence ---
        # ----------------------------------------

        elapseTime = datetime.datetime.now() - beginTime
        backup_file_size = cmd.run_remote(f"du -m {full_path_backup_file}", out_format="last").split()[0]
        debug(f"The size of the backup file is : {backup_file_size} G")
        final_cleanup(
            full_path_backup_file,
            backup_file_size,
            full_backup_dir,
            vm_backup_dir,
            vm_max_backups,
        )

        if not check_all_backups_success(vm_backup_dir):
            verbose(
                "Cleanup needed - not all backup history is successful",
                level=logging.WARNING,
            )
            this_status = "warning"
        suffix_msg = f"elapse:{str(elapseTime.seconds / 60)} size:{backup_file_size}G"
        if this_status == "success":
            success_cnt += 1
            verbose(f"{BASE_NAME} vm-export {vm_name} - *** Success *** ; {suffix_msg}")
            status_log_vm_export_end(server_name, f"SUCCESS {vm_name},{suffix_msg}")

        elif this_status == "warning":
            warning_cnt += 1
            verbose(f"{BASE_NAME} vm-export {vm_name} - *** WARNING *** ; {suffix_msg}")
            status_log_vm_export_end(server_name, f"WARNING {vm_name},{suffix_msg}")

        else:
            # this should never occur since all errors do a continued on to the next vm_name
            error_cnt += 1
            verbose(f"{BASE_NAME} vm-export {vm_name} - +++ ERROR-INTERNAL +++ ; {suffix_msg}")
            status_log_vm_export_end(server_name, f"ERROR-INTERNAL {vm_name},{suffix_msg}")

    # end of for vm_parm in config['vm-export']:
    ######################################################################

    verbose()
    df_snapshots(f"Space status", min_capacity=1)

    # gather a final vmbackup.py status
    summary = f"Success:{success_cnt}; Warnings:{warning_cnt}; Errors:{error_cnt}"

    status_log = config.get(section, "status_log", fallback=DEFAULT_STATUS_LOG)
    sub_suffix = f"{os.uname()[1]} {BASE_NAME}.py"
    if error_cnt > 0:
        status_log_end(server_name, f"ERROR,{summary}")
        send_email(f'ERROR {sub_suffix}', status_log)
        verbose(f"{BASE_NAME} ended - ** ERRORS DETECTED ** - {summary}")
    elif warning_cnt > 0:
        status_log_end(server_name, f"WARNING,{summary}")
        send_email(f'WARNING {sub_suffix}', status_log)
        verbose(f"{BASE_NAME} ended - ** WARNING(s) ** - {summary}")
    else:
        status_log_end(server_name, f"SUCCESS,{summary}")
        send_email(f'Success {sub_suffix}', status_log)
        verbose(f"{BASE_NAME} ended - Success - {summary}")

    # done with main()
    ######################################################################


def get_vm_max_backups(vm_parm):
    # get max_backups from optional vm-export=VM-NAME:MAX-BACKUP override
    # NOTE - if not present then return config['max_backups']
    data = vm_parm.split(":")
    try:
        res = int(data[1])
    except (IndexError, ValueError):
        int(config.get(section, "max_backups", fallback=DEFAULT_MAX_BACKUPS))
        res = int(config.get(section, "max_backups", fallback=DEFAULT_MAX_BACKUPS))

    return res if res > 0 else int(config.get(section, "max_backups", fallback=DEFAULT_MAX_BACKUPS))


def is_vm_backups_valid(vm_parm):
    """
    Verify that the VM name is valid in the format : <name>:<MaxBackups>
    where MaxBackups is integer and > 0

    Args:
        vm_parm (str):  the vm name from configuration

    Return:
        bool : True if the full name is valid, False otherwise
    """
    data = vm_parm.split(":")
    try:
        res = int(data[1]) > 0
    except IndexError:
        res = True
    except ValueError:
        res = False

    return res


def get_vm_name(vm_parm):
    # get vm_name from optional vm-export=VM-NAME:MAX-BACKUP override
    return vm_parm.split(":")[0]


def verify_vm_name(tmp_vm_name):
    debug(f"Verify {tmp_vm_name}")
    vm = session.xenapi.VM.get_by_name_label(tmp_vm_name)
    vmref = [x for x in session.xenapi.VM.get_by_name_label(tmp_vm_name) if not session.xenapi.VM.get_is_a_snapshot(x)]
    debug(f"VM refs of {tmp_vm_name} are : {vmref}")
    if len(vmref) > 1:
        verbose(f"duplicate VM name found: {tmp_vm_name} | {vmref}", level=logging.ERROR)
        return f"ERROR more than one vm with the name {tmp_vm_name}"
    elif len(vm) == 0:
        return f"ERROR no machines found with the name {tmp_vm_name}"
    return vm[0]


def gather_vm_meta(vm_object, tmp_full_backup_dir):
    global vm_uuid
    global xvda_uuid
    global xvda_name_label
    vm_uuid = ""
    xvda_uuid = ""
    xvda_name_label = ""
    tmp_error = ""

    vm_record = session.xenapi.VM.get_record(vm_object)
    vm_uuid = vm_record["uuid"]

    verbose("Exporting VM metadata XML info")
    command = f"vm-export metadata=true uuid={vm_uuid} filename={tmp_full_backup_dir}/vmm.tar"
    if cmd.run_xe(command, out_format="rc") != 0:
        verbose(f"Cannot export metadata : {command}", level=logging.WARNING)
        # this_status = "warning"
        # non-fatal - finish processing for this vm
    else:
        debug(f"Metadata was exported into {tmp_full_backup_dir}/vmm.tar")
        command = f'tar -xOf "{tmp_full_backup_dir}/vmm.tar" > "{tmp_full_backup_dir}/vm-metadata.xml"'
        debug(f"Try to untar : {command}")
        if cmd.run_remote(command, out_format="rc") != 0:
            verbose(f"Cannot untar metadata : {command}", level=logging.WARNING)
            # this_status = "warning"
            # non-fatal - finish processing for this vm
        else:
            debug(f"Untar of {tmp_full_backup_dir}/vmm.tar succeed")
            command = f"/usr/bin/xmllint -format "
            command += f'-o "{tmp_full_backup_dir}/vm-metadata.xml" "{tmp_full_backup_dir}/vm-metadata.xml"'
            debug(f"Try to reformat the xml file : {command}")
            if cmd.run_remote(command, out_format="rc") != 0:
                verbose(f"Cannot lint the xml file metadata : {command}", level=logging.WARNING)
                # this_status = "warning"
                # non-fatal - finish processing for this vm
            else:
                debug("Re-format the xml succeed")
    title("vm-export metadata end")

    verbose("Writing disk info")
    vbd_cnt = 0
    debug(f"list of VDBs is : {vm_record['VBDs']}")
    for vbd in vm_record["VBDs"]:
        verbose(f"vbd: {vbd}")
        vbd_record = session.xenapi.VBD.get_record(vbd)
        # For each vbd, find out if it's a disk
        if vbd_record["type"].lower() != "disk":
            continue
        vbd_record_device = vbd_record["device"]
        if vbd_record_device == "":
            # not normal - flag as warning.
            # this seems to occur on some vms that have not been started in a long while,
            #   after starting the vm this blank condition seems to go away.
            tmp_error += f"empty vbd_record[device] on vbd: {vbd} "
            # if device is not available then use counter as an alternate reference
            vbd_cnt += 1
            vbd_record_device = vbd_cnt

        vdi_record = session.xenapi.VDI.get_record(vbd_record["VDI"])
        verbose(f"disk: {vdi_record['name_label']} - begin")

        # now write out the vbd info.
        device_path = f"{tmp_full_backup_dir}/DISK-{vbd_record_device}"
        debug(f"Try to create {device_path}")
        if cmd.run_remote(f"mkdir -p {device_path}", out_format="rc") == 0:
            debug(f" {device_path} created !")
        else:
            debug(f" {device_path} didn't created !")

        vbd_file = f"{device_path}/vbd.cfg"
        vdi_file = f"{device_path}/vdi.cfg"

        vbd_output = list()
        for key in ["userdevice", "bootable", "mode", "type", "unpluggable", "empty"]:
            vbd_output.append(f"{key}={vbd_record[key]}")
        vbd_output.append(f"orig_uuid={vbd_record['uuid']}")
        cmd.write_to_file(filename=vbd_file, data=vbd_output)

        # now write out the vdi info.
        vdi_output = list()
        for key in ["name_label", "name_description", "virtual_size", "type", "sharable", "read_only"]:
            vdi_output.append(f"{key}={vdi_record[key]}")

        sr_uuid = session.xenapi.SR.get_record(vdi_record["SR"])["uuid"]
        vdi_output.append("orig_uuid={vdi_record['uuid']}")
        vdi_output.append(f"orig_sr_uuid={sr_uuid}")
        cmd.write_to_file(filename=vdi_file, data=vdi_output)

        # other_config and qos stuff is not backed up
        if vbd_record_device == "xvda":
            xvda_uuid = vdi_record["uuid"]
            xvda_name_label = vdi_record["name_label"]

    # Write metadata files for vifs.  These are put in VIFs directory
    verbose("Writing VIF info")
    for vif in vm_record["VIFs"]:
        vif_record = session.xenapi.VIF.get_record(vif)
        verbose(f"Writing VIF: {vif_record['device']}")
        device_path = f"{tmp_full_backup_dir}/VIFs"
        debug(f"Try to create {device_path}")
        if cmd.run_remote(f"mkdir -p {device_path}", out_format="rc") == 0:
            debug(f" {device_path} created !")
        else:
            debug(f" {device_path} didn't created !")
        vif_file = f"{device_path}/vif-{vif_record['device']}vbd.cfg"
        network_name = session.xenapi.network.get_record(vif_record["network"])["name_label"]
        vif_output = list()
        for key in ["device", "MTU", "MAC", "other_config", "uuid"]:
            vif_output.append(f"{key}={vif_record[key]}")
        vif_output.append(f"network_name_label={network_name}")
        cmd.write_to_file(filename=vif_file, data=vif_output)

    return tmp_error


def final_cleanup(
    tmp_full_path_backup_file,
    tmp_backup_file_size,
    tmp_full_backup_dir,
    tmp_vm_backup_dir,
    tmp_vm_max_backups,
):
    # mark this a successful backup, note: this will 'touch' a file named 'success'
    # if backup size is greater than 60G, then nfs server side compression occurs
    if int(tmp_backup_file_size) > 60:
        verbose(f"*** LARGE FILE > 60G: {tmp_full_path_backup_file} : {tmp_backup_file_size}G")
        # forced compression via background gzip (requires nfs server side script)
        cmd.run_remote(f"touch {tmp_full_backup_dir}/success_compress")
        verbose(f"*** success_compress: {tmp_full_path_backup_file} : {tmp_backup_file_size}G")
    else:
        cmd.run_remote(f"touch {tmp_full_backup_dir}/success")
        verbose(f"*** success: {tmp_full_path_backup_file} : {tmp_backup_file_size}G")

    # Remove the oldest if more than tmp_vm_max_backups
    dir_to_remove = get_dir_to_remove(tmp_vm_backup_dir, tmp_vm_max_backups)
    while dir_to_remove:
        verbose(f"Deleting oldest backup {tmp_vm_backup_dir}/{dir_to_remove} ")
        # remove dir - if throw exception then stop processing
        cmd.run_remote(f" rm -rf {tmp_vm_backup_dir}/{dir_to_remove}")
        dir_to_remove = get_dir_to_remove(tmp_vm_backup_dir, tmp_vm_max_backups)


def pre_cleanup(tmp_vm_backup_dir, tmp_vm_max_backups):
    """
    Run a cleanup in the backup directory, by deleting the oldest backup and
    leave only (max_backup -1) backups.

    Args:
        tmp_vm_backup_dir (str): the path of the backups
        tmp_vm_max_backups (int): the number of maximum backup that can saved
    """
    debug(f" ==== tmp_vm_backup_dir: {tmp_vm_backup_dir}")
    debug(f" ==== tmp_vm_max_backups: {tmp_vm_max_backups}")
    verbose(f"success identifying directory : {tmp_vm_backup_dir}")
    # Remove the oldest if more than tmp_vm_max_backups -1
    pre_vm_max_backups = tmp_vm_max_backups - 1
    verbose(f"pre_VM_max_backups: {pre_vm_max_backups} ")
    if pre_vm_max_backups < 1:
        verbose(f"No pre_cleanup needed for {tmp_vm_backup_dir} ")
    else:
        dir_to_remove = get_dir_to_remove(tmp_vm_backup_dir, tmp_vm_max_backups)
        while dir_to_remove:
            verbose(f"Deleting oldest backup {tmp_vm_backup_dir}/{dir_to_remove}")
            # remove dir - if throw exception then stop processing
            cmd.path_delete(f"{tmp_vm_backup_dir}/{dir_to_remove}")
            dir_to_remove = get_dir_to_remove(tmp_vm_backup_dir, tmp_vm_max_backups)


# cleanup old unsuccessful backup and create new full_backup_dir
def process_backup_dir(tmp_vm_backup_dir):
    """
    Cleanup the directory of last backup - if it didn't succeed, and create
    a directory for the current backup.

    Args:
        tmp_vm_backup_dir (str):
    :return:
    """
    # if last backup was not successful, then delete it
    verbose(f"Check for last ** Unsuccessful ** backup: {tmp_vm_backup_dir}")
    dir_not_success = get_last_backup_dir_that_failed(tmp_vm_backup_dir)
    if dir_not_success:
        verbose(f"Delete last ** Unsuccessful ** backup {tmp_vm_backup_dir}/{dir_not_success}")
        # remove last unsuccessful backup  - if throw exception then stop processing
        if cmd.run_remote(f"rm -rf {tmp_vm_backup_dir}/{dir_not_success}", out_format="rc") == 0:
            verbose(f"The directory {tmp_vm_backup_dir}/{dir_not_success} was deleted successfully")
        else:
            verbose(f"Failed to delete {tmp_vm_backup_dir}/{dir_not_success} !", level=logging.WARNING)
    else:
        verbose(f"There are no Unsuccessful backup to delete at : {tmp_vm_backup_dir}/{dir_not_success}")

    # create new backup dir
    return create_full_backup_dir(tmp_vm_backup_dir)


# Setup full backup dir structure
def create_full_backup_dir(vm_base_path):
    """
    Make sure that the backup directory is existed. if not it will be created.

    Args:
        vm_base_path (str): the base path for the backup directory

    Return:
        str : the full path of the backup directory
    """
    # Check that directory exists

    tmp_backup_dir = f"{vm_base_path}/backup-{time.strftime('%Y%m%d-%H%M%S')}"
    verbose(f"new backup_dir: {tmp_backup_dir}")

    debug(f"Make sure that the directory {tmp_backup_dir} exist.")
    if cmd.run_remote(f"mkdir -p {tmp_backup_dir}", out_format="rc") != 0:
        verbose(f"Cannot create directory {tmp_backup_dir}")
        sys.exit(1)
    else:
        debug(f"The directory {tmp_backup_dir} created successfully")
    return tmp_backup_dir


def get_dir_to_remove(path, numbackups):
    """
    Find the oldest backup and select for deletion

    Args:
        path (str): path of the backups
        numbackups (int): number of maximum backups to save

    Return:
        str / bool : the path of the backup to delete, otherwise False
    """
    dirs = cmd.dir_list(path)
    if len(dirs) > numbackups and len(dirs) > 1:
        return dirs[0]
    else:
        return False


def get_last_backup_dir_that_failed(path):
    """
    Look for the last backup that failed.

    Args
        path (str): the path for the backups

    Return:
        str / bool : the path of the failed backup, otherwise False
    """
    # if the last backup dir was not success, then return that backup dir
    debug(f"Check if {path} exists.")
    dirs = cmd.dir_list(path)
    verbose(f"All backup directories are : {dirs}")
    if len(dirs) < 1:
        return False
    # note: dirs[-1] is the last entry
    debug(f"The latest backup dir is: {dirs[-1]}")
    last_backup_path = f"{path}/{dirs[-1]}/success*"
    debug(f"Looking for file(s) : {last_backup_path}")
    res = cmd.dir_list(last_backup_path)
    if len(res) == 0:
        debug(f"no success file(s) at {dirs[-1]}")
        return dirs[-1]
    else:
        debug(f"No failed backup exists : {res}")
        return False


def check_all_backups_success(path):
    """
    Check that the last backup completed successfully

    Args
        path (str): the path of the backups

    Return:
        bool : True if the last backup completed successfully otherwise False
    """
    # expect at least one backup dir, and all should be successful
    dirs = cmd.dir_list(path)
    if len(dirs) == 0:
        return False
    res = cmd.dir_list(f"{path}/{dirs[-1]}/success*")
    if len(res) == 0:
        verbose(f"Directory not successful - {dirs[-1]}", level=logging.WARNING)
        return False
    return True


def backup_pool_metadata(svr_name):
    """
    Backing up the db pool metadata

    Args:
        svr_name (str): the name of the XEN-server

    Return:
        bool : True if dumped OK, otherwise False
    """
    # xe-backup-metadata can only run on master
    if not is_xe_master():
        verbose("** ignore: NOT master")
        return True

    metadata_base = os.path.join(
        config.get(section, "backup_dir", fallback=DEFAULT_BACKUP_DIR),
        "METADATA_" + svr_name,
    )
    if cmd.run_remote(f"mkdir -p {metadata_base}", out_format="rc") != 0:
        verbose(f"creating directory {metadata_base} Failed", level=logging.ERROR)
        return False

    metadata_file = f"{metadata_base}/pool_db_{time.strftime('%Y/%m/%d-%H%M%S')}.dump"

    command = f"pool-dump-database file-name='{metadata_file}'"
    verbose(command)
    if cmd.run_xe(command, out_format="rc") != 0:
        verbose("Failed to backup pool metadata", level=logging.ERROR)
        return False

    return True


def df_snapshots(log_msg, min_capacity=1024):
    """
    Display the Backup dir capacity usage using the 'df' command, if there is not
    enough capacity (in GB) exit the script.

    Args:
        log_msg (str): message string to display before the command output
        min_capacity (int): the minimum capacity that need to be.
    """
    command = f"df -T {config.get(section, 'backup_dir', fallback=DEFAULT_BACKUP_DIR)}"
    verbose(f"{log_msg} : {command}")
    result = cmd.run_remote(command, out_format="list")
    for line in result:
        verbose(line)
    avail = int(result[-1].split()[-3]) / (1024 * 1024)
    verbose(f"The Available storage for backup is : {int(avail)} GB")
    if avail < min_capacity:
        verbose(f"There is not enough capacity < {str(min_capacity/1024)} T")
        sys.exit(3)


def send_email(subject, body_fname):
    if not config.get(section, "email_enable", fallback=MAIL_ENABLE):
        verbose("Not Configured to send emails.")
        return

    to = config.get(section, "mail_to_addr", fallback=MAIL_TO_ADDR)
    send_from = config.get(section, "mail_from_addr", fallback=MAIL_FROM_ADDR)
    smtp_send_retries = 3
    smtp_send_attempt = 0

    message = open(body_fname, "r").read()

    msg = MIMEText(message)
    msg["subject"] = subject
    msg["From"] = send_from
    msg["To"] = to

    while smtp_send_attempt < smtp_send_retries:
        smtp_send_attempt += 1
        if smtp_send_attempt > smtp_send_retries:
            verbose("Send email count limit exceeded")
            sys.exit(1)
        try:
            # note if using an ipaddress in MAIL_SMTP_SERVER,
            # then may require smtplib.SMTP(MAIL_SMTP_SERVER, local_hostname="localhost")

            # Optional use of SMTP user authentication via TLS
            #
            # If so, comment out the next line of code and uncomment/configure
            # the next block of code. Note that different SMTP servers will require
            # different username options, such as the plain username, the
            # domain\username, etc. The "From" email address entry must be a valid
            # email address that can be authenticated  and should be configured
            # in the MAIL_FROM_ADDR variable along with MAIL_SMTP_SERVER early in
            # the script. Note that some SMTP servers might use port 465 instead of 587.
            s = smtplib.SMTP(config.get(section, "mail_smtp", fallback=MAIL_SMTP_SERVER))
            # ### start block
            # username = 'MyLogin'
            # password = 'MyPassword'
            # s = smtplib.SMTP(MAIL_SMTP_SERVER, 587)
            # s.ehlo()
            # s.starttls()
            # s.login(username, password)
            # ### end block
            s.sendmail(send_from, to.split(","), msg.as_string())
            s.quit()
            break
        except socket.error as e:
            verbose(f"Exception: socket.error -  {e}")
            time.sleep(5)
        except smtplib.SMTPException as e:
            verbose(f"Exception: SMTPException - {e.message}")
            time.sleep(5)

    # trunc status log file after email it
    status_log = config.get(section, "status_log", fallback=DEFAULT_STATUS_LOG)
    open(status_log, 'w').close()

def is_xe_master():
    """
    Test to see if we are running on xe master (remotely of locally)

    Return:
        bool : True if XEN is master
    """
    command = "pool-list params=master --minimal"
    master_uuid = cmd.run_xe(command, out_format="last")

    hostname = os.uname()[1]
    command = f"host-list name-label={hostname} --minimal"
    host_uuid = cmd.run_xe(command, out_format="last")

    if host_uuid == master_uuid:
        return True

    return False


def check_vm_in_server(full_name, vm):
    """
    Compare the configuration VM name (can be REGEX) with part of max_backups
    to VM name from the server.
    if it matches, return the new name with the max_backup and true, otherwise
    it returns '' and false.

    Args:
        full_name (str): The VM name from configuration (can be REGEX)
                         include max_backup
        vm (str): The VM name from the server (xen-server)

    Return:
        str, bool : The name, and true if found
    """
    found_match = False
    new_value = ""
    values = full_name.split(":")
    vm_name = values[0]
    vm_backups_part = values[1] if len(values) > 1 else ""
    normal_name = re.match("^[\w\s\-\_]+$", vm_name) is not None
    if not normal_name and not re.compile(vm_name):
        verbose(f"invalid regex: {key} = {vm_name}", level=logging.ERROR)
    if (normal_name and vm_name == vm) or (not normal_name and re.match(vm_name, vm)):
        if vm_backups_part == "":
            new_value = vm
        else:
            new_value = f"{vm}:{vm_backups_part}"
        found_match = True
    return new_value, found_match


def is_config_valid():
    """
    Verify that all configuration is valid

    Return:
        bool : True if configuration is valid, else False
    """
    global vdi_export
    global backup_vms
    for key in ["pool_db_backup", "max_backups"]:
        if not config.get(section, key, fallback="").isdigit():
            verbose(
                f"Config {key} non-numeric -> {config.get(section, key, fallback='')}",
                level=logging.ERROR,
            )
            return False

    if int(config.get(section, "pool_db_backup", fallback="")) not in [0, 1]:
        verbose(
            f"Config pool_db_backup out of range -> {config.get(section, 'pool_db_backup', fallback='')}",
            level=logging.ERROR,
        )
        return False

    if int(config.get(section, "max_backups", fallback="")) < 1:
        verbose(
            f"Config max_backups out of range -> {config.get(section, 'max_backups', fallback='')}",
            level=logging.ERROR,
        )
        return False

    if config.get(section, "vdi_export_format", fallback="") not in ["raw", "vhd"]:
        verbose(
            f"Config vdi_export_format invalid -> {config.get(section, 'vdi_export_format', fallback='')}",
            level=logging.ERROR,
        )
        return False

    if cmd.path_exists(config.get(section, "backup_dir", fallback="")) != 0:
        verbose(
            f"Config backup_dir does not exist -> {config.get(section, 'backup_dir', fallback='')}",
            level=logging.ERROR,
        )
        return False

    tmp_return = True
    for vm_parm in backup_vms + vdi_export:
        if not is_vm_backups_valid(vm_parm):
            verbose(f"vm_max_backup is invalid - {vm_parm}", level=logging.ERROR)
            tmp_return = False

    # Remove all excluded vms from the all vm's list
    for inc_vm in config[section]["exclude"].split(","):
        for vm in all_vms:
            new_value, found_match = check_vm_in_server(inc_vm, vm)
            if found_match:
                all_vms.remove(new_value)
                verbose(f"The vm {new_value} will be excluded !")

    # Verify that no duplicat name exists
    vdi_export = select_vms(vdi_export)
    backup_vms = select_vms(backup_vms)
    return tmp_return


def get_all_vms():
    """
    Function to retrieve the list of all the VM's on the xen-Server

    Return:
        list
    """
    global all_vms
    command = "vm-list is-control-domain=false is-a-snapshot=false params=name-label --minimal"
    all_vms = cmd.run_xe(command, out_format="string").split(",")


def write_status_log_msg(op, server, script=f"{BASE_NAME}.py", status=""):
    date = time.strftime("%Y/%m/%d %H:%M:%S")
    msg = f"{date},{script},{server},{op} {status}\n"
    with open(config.get(section, "status_log", fallback=DEFAULT_STATUS_LOG), "a") as fh:
        fh.write(msg)
    debug(msg.strip())


def status_log_begin(server):
    write_status_log_msg(op="begin", server=server)


def status_log_end(server, status):
    write_status_log_msg(op="end", server=server, status=status)


def status_log_vm_export_begin(server, status):
    write_status_log_msg(op="begin", server=server, status=status, script="vm-export")


def status_log_vm_export_end(server, status):
    write_status_log_msg(op="end", server=server, status=status, script="vm-export")


def status_log_vdi_export_begin(server, status):
    write_status_log_msg(op="begin", server=server, status=status, script="vdi-export")


def status_log_vdi_export_end(server, status):
    write_status_log_msg(op="end", server=server, status=status, script="vdi-export")


if __name__ == "__main__":
    # Parsing the command line arguments
    arg = argument.Arguments()

    # Check if just asking for help
    arg.help_check()

    section = arg.args.section.upper()

    # Getting variables from the CLI
    cfg_file = arg.args.config_file

    verbose(f"Going to use '{cfg_file}' as configuration file")
    if cfg_file != CFG_FILE:
        config.read(cfg_file)

    try:
        password = arg.get_password(pass_file=config.get(section, "xen_password_file", fallback=""))
    except Exception:
        verbose("No Password defined", level=logging.ERROR)
        password = ""
    preview = arg.is_preview()
    compress = arg.is_compress()
    ignore_extra_keys = arg.is_ignore_extra_keys()
    pre_clean = arg.is_pre_clean()

    backup_vms = config.get(section, "vm-export", fallback="").split(",") + arg.args.vm_export
    vdi_export = config.get(section, "vdi-export", fallback="").split(",")

    if not arg.args.verbose and config.get(section, "verbose", fallback="true") == "false":
        verbose = no_verbose

    if arg.args.debug or config.get(section, "debug", fallback="true") == "true":
        logging.basicConfig(level=logging.DEBUG)
    else:
        debug = no_debug

    cmd.user(config.get(section, "xen_user", fallback=DEFAULT_USER))
    cmd.host(config.get(section, "xen_server", fallback=DEFAULT_XENSERVER))

    debug(f"Xen-server is {cmd.host()}, and going to connect with user {cmd.user()}")

    get_all_vms()

    debug(f"The list of all VMs in the Xen-Server: ({cmd.host()}) are : {all_vms}")

    debug("The list of all variables in the configuration is :")
    for key in config[section]:
        verbose(f"  {key} = {config[section][key]}")

    verbose("Validating the configuration")
    if not is_config_valid():
        verbose("Configuration settings is invalid", level=logging.ERROR)
        sys.exit(1)
    verbose("All configuration variables are valid.")
    debug(f"The root password is : {password}")
    debug(f"The VM's that are going to be backed up are : {backup_vms}")
    debug(f"The VM's that are going to be VDI export only up are : {vdi_export}")

    if len(backup_vms) == 0 and len(vdi_export) == 0:
        verbose("No VMs loaded", level=logging.ERROR)
        sys.exit(1)

    # acquire a xapi session by logging in
    debug(f"Try to open an API session to {config.get(section, 'xen_server', fallback=DEFAULT_XENSERVER)}")
    username = config.get(section, "xen_user", fallback=DEFAULT_USER)
    try:
        session = XenAPI.Session(f"http://{config.get(section, 'xen_server', fallback=DEFAULT_XENSERVER)}/")
        debug(f"session is: {session}")
        debug(f"Try to open session with User:{username} , Password:{password}")
        session.login_with_password(username, password)
        hosts = session.xenapi.host.get_all()
        debug(f"All hosts are : {hosts}")
    except XenAPI.Failure as e:
        verbose(f"[{e}] ===>")
        if e.details[0] == "HOST_IS_SLAVE":
            session = XenAPI.Session("http://" + e.details[1])
            session.xenapi.login_with_password(username, password)
            hosts = session.xenapi.host.get_all()
        else:
            verbose(f"XenAPI authentication error [{e}]", level=logging.ERROR)
            sys.exit(1)

    if preview:
        # check for duplicate names
        verbose("Checking all VMs for duplicate names ...")
        for vm in all_vms:
            vmref = [x for x in session.xenapi.VM.get_by_name_label(vm) if not session.xenapi.VM.get_is_a_snapshot(x)]
            debug(f"All VM refs of {vm} are : {vmref}")
            if len(vmref) > 1:
                verbose(f"Duplicate VM name found: {vm} | {vmref}", level=logging.ERROR)
            else:
                verbose(f"No Duplication for {vm}")

        verbose("SUCCESS preview of parameters")
        sys.exit()

    verbose("SUCCESS preview of parameters")

    try:
        main(session)

    except Exception as e:
        verbose(e)
        verbose(f"Session EXCEPTION - {sys.exc_info()[0]}", level=logging.ERROR)
        verbose(f"NOTE: see {BASE_NAME} output for details", level=logging.ERROR)
        raise
