#!/bin/sh
# Signs PodVault.app (ad hoc: no developer certificate) so that macOS remembers the
# permissions it was given — the iPod (a removable volume), a library on a network drive.
#
#   packaging/sign.sh /path/to/PodVault.app
#
# The process that touches the files is Contents/MacOS/python, not the bundle's own
# launcher script. An ad-hoc signature's usual requirement is the file's own hash, so the
# permission given to one was never the other's, and macOS asked again at every start.
# Both are signed here with the same requirement, the app's identifier, which both meet.
set -eu
APP="$1"
ID=io.github.nzmnq.podvault
REQ="designated => identifier \"$ID\""
codesign --force --sign - --identifier "$ID" -r="$REQ" "$APP/Contents/MacOS/python"
codesign --force --sign - --identifier "$ID" -r="$REQ" "$APP"
