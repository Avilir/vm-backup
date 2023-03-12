#!/usr/bin/env python3

import argparse
import base64

parser = argparse.ArgumentParser(add_help=True)

parser.add_argument(
    "-p",
    "--password",
    help="xen server 'root' password",
    required=True,
)

parser.add_argument(
    "--password-file",
    help="file name to store the obscured 'root' password of the Xen-Server",
    required=True,
)


if __name__ == "__main__":
    args = parser.parse_args()
    with open(args.password_file, "wb") as fh:
        fh.write(base64.b64encode(args.password.encode("UTF-8")))
    print(f"password file saved to: {args.password_file}")
