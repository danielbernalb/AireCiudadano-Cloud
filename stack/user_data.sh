#!/bin/bash
LOG_LOCATION=$HOME
exec > >(tee -i $LOG_LOCATION/userdata.txt)
exec 2>&1
sudo apt update && sudo apt install -y jq unzip git snapd

#====================VARIABLES==============================
#export PUBLIC_IP=ip a l enp0s3 | awk '($1=="inet"){split($2,a,"/");print a[1]}'
export PUBLIC_IP=sensor.aireciudadano.com
export GRAFANA_ADMIN_PASSWORD="daniel2022"
#===========================================================

#==============Initialize /data if needed===================
#Ensure there is a directory created for the applications persistent data
for application in prometheus pushgateway grafana mosquitto letsencrypt
do
  if [ ! -d /data/$application ]; then
    sudo mkdir -p /data/$application
    sudo chown -R $USER:$USER /data/$application
    sudo chmod o+w /data/$application
  fi
done
#===========================================================

#===============Install K8s and helm3=======================
# --- BEGIN robust microk8s install ---
set -euo pipefail
TARGET_USER=${SUDO_USER:-$USER}
echo "Using TARGET_USER=${TARGET_USER}"

# ensure snapd running and ready
sudo apt update
sudo apt install -y snapd
sudo systemctl enable --now snapd.socket
sudo systemctl restart snapd
# wait a bit for snapd socket and store connections
sleep 8

# debug info (logged)
echo "=== SNAP VERSION ==="
snap version || true
echo "=== LSB RELEASE ==="
lsb_release -a || true
echo "=== UNAME -M ==="
uname -m || true

# abort any stuck snap changes (safe)
for cid in $(snap changes | awk 'NR>1 {print $1, $4}' | awk '$2=="Doing" {print $1}'); do
  echo "Aborting pending snap change $cid"
  sudo snap abort "$cid" || true
done

# show available channels for microk8s (helpful for debugging)
echo "=== SNAP INFO (microk8s channels) ==="
snap info microk8s | sed -n '1,200p'

# remove previous microk8s if present (clean install)
if snap list | grep -q '^microk8s\b'; then
  echo "Removing existing microk8s (purge)"
  sudo snap remove --purge microk8s || true
  sleep 4
fi

# give snapd a bit more time after purge
sleep 4

# Try install requested channel (1.25/stable)
sudo snap install microk8s --classic --channel=1.25/stable || {
  echo "First attempt to install channel failed — dumping debug info and exiting with nonzero"
  snap changes || true
  snap list || true
  exit 1
}

# post-install: ensure group membership for user's account (not root)
sudo usermod -a -G microk8s "${TARGET_USER}"
echo "Added ${TARGET_USER} to group microk8s"

# enable common addons (wait until microk8s is fully started)
# give microk8s services a moment to initialise
sleep 6
sudo microk8s status --wait-ready
sudo microk8s enable dns helm3

# aliases for shell (for non-root user)
echo "alias sudo='sudo '" >> "${HOME}/.bashrc"
echo "alias kubectl='microk8s.kubectl'" >> "${HOME}/.bashrc"
echo "alias helm='microk8s.helm3'" >> "${HOME}/.bashrc"
# --- END robust microk8s install ---
#===========================================================

#================Install anaire cloud stack=================
cd $HOME
git clone --branch cambios36_6sept2025 https://github.com/danielbernalb/aireciudadano-cloud.git
ln -s anaire-cloud/stack/virtualbox/delete_stack.sh
ln -s anaire-cloud/stack/virtualbox/upgrade_stack.sh
ln -s anaire-cloud/stack/virtualbox/start_stack.sh
sudo microk8s.helm3 install --set tls=true --set publicIP=$PUBLIC_IP --set grafanaAdminPass=$GRAFANA_ADMIN_PASSWORD aireciudadanostack aireciudadano-cloud/stack/aireciudadanocloud
#===========================================================
