#!/bin/bash
# Start dbus system
sudo mkdir -p /var/run/dbus && \
sudo dbus-daemon --config-file=/usr/share/dbus-1/system.conf --print-address

# Start shell to start keyring
eval "$(dbus-launch --sh-syntax)"

# Create necessary non-root dirs for keyring
mkdir -p ~/.cache
mkdir -p ~/.local/share/keyrings # where the automatic keyring is created

# 1. Create the keyring manually with a dummy password in stdin
eval "$(printf '\n' | gnome-keyring-daemon --unlock)"

# 2. Start the daemon, using the password to unlock the just-created keyring:
eval "$(printf '\n' | /usr/bin/gnome-keyring-daemon --start)"

# Disable connectivity check
printf "[connectivity]\nuri=http://www.archlinux.org/check_network_status.txt\ninterval=0" | sudo tee /etc/NetworkManager/conf.d/20-connectivity.conf

# Add polkit rules so that user can make any actions
printf "\npolkit.addRule(function(action, subject) {\n\tif (action.id.indexOf('org.freedesktop.NetworkManager.') == 0 && subject.isInGroup('network')) {\n\t\treturn polkit.Result.YES;\n\t}\n});\n" | sudo tee /etc/polkit-1/rules.d/50-org.freedesktop.NetworkManager.rules

# Start network manager
sudo /sbin/NetworkManager start

if [ -f .env ]; then
  echo 'find local .env ~ load new env';
  export $(cat .env | xargs);
  env;
fi

exec "$@";
