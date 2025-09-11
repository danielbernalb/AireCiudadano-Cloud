#!/bin/bash
# Actualizar user_data.sh para K3s
LOG_LOCATION=$HOME
exec > >(tee -i $LOG_LOCATION/userdata.txt)
exec 2>&1
sudo apt update && sudo apt install -y jq unzip git

#====================VARIABLES==============================
export PUBLIC_IP=sensor.aireciudadano.com
export GRAFANA_ADMIN_PASSWORD="daniel2022"

#==============Initialize /data if needed===================
for application in prometheus pushgatewaypython grafana mosquitto letsencrypt
do
    if [ ! -d /data/$application ]; then
        sudo mkdir -p /data/$application
        sudo chown -R $USER:$USER /data/$application
        sudo chmod o+w /data/$application
    fi
done

#===============Install K3s=======================
curl -sfL https://get.k3s.io | INSTALL_K3S_EXEC="server --disable=traefik --disable=servicelb --write-kubeconfig-mode 644" sh -
export KUBECONFIG=/etc/rancher/k3s/k3s.yaml

# Esperas de readiness
kubectl wait --for=condition=Ready node --all --timeout=180s
kubectl -n kube-system rollout status deploy/coredns --timeout=180s || true
kubectl -n kube-system rollout status deploy/local-path-provisioner --timeout=180s || true

# Configurar kubectl
echo "export KUBECONFIG=/etc/rancher/k3s/k3s.yaml" >> $HOME/.bashrc
echo "alias kubectl='k3s kubectl'" >> $HOME/.bashrc

# Instalar Helm
curl https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash

#================Restore data and install stack=================
cd $HOME
git clone --branch cambios40_11sept2025_k3s https://github.com/danielbernalb/aireciudadano-cloud.git
# Instalar el stack con Helm
sudo helm install --set tls=true --set publicIP=$PUBLIC_IP --set grafanaAdminPass=$GRAFANA_ADMIN_PASSWORD aireciudadanostack aireciudadano-cloud/stack/aireciudadanocloud
#===========================================================