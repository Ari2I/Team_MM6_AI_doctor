#!/bin/sh
set -eu
: "${WEB_USER:?WEB_USER is required}"
: "${WEB_PASSWORD:?WEB_PASSWORD is required}"
# htpasswd reads the password from stdin; do not log it or pass it as an argument.
umask 077
printf '%s\n' "$WEB_PASSWORD" | htpasswd -i -B -c /etc/nginx/.htpasswd "$WEB_USER" >/dev/null 2>&1
chown root:nginx /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd
