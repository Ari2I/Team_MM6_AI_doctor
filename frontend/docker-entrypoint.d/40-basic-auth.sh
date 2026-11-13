#!/bin/sh
set -eu
if [ -z "${WEB_USER:-}" ] || [ -z "${WEB_PASSWORD:-}" ]; then
    printf '%s\n' 'auth_basic off;' > /etc/nginx/basic-auth.conf
    exit 0
fi
# htpasswd reads the password from stdin; do not log it or pass it as an argument.
umask 077
printf '%s\n' "$WEB_PASSWORD" | htpasswd -i -B -c /etc/nginx/.htpasswd "$WEB_USER" >/dev/null 2>&1
chown root:nginx /etc/nginx/.htpasswd
chmod 640 /etc/nginx/.htpasswd
printf '%s\n' 'auth_basic "Demo access";' 'auth_basic_user_file /etc/nginx/.htpasswd;' > /etc/nginx/basic-auth.conf
