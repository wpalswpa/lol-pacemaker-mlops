#!/bin/bash
# Lightsail 인스턴스(lol-mini)를 처음 켤 때 한 번 돈다(root, Amazon Linux 2023). 로그: /var/log/cloud-init-output.log
# 서버에는 Docker 만 깐다. 이미지는 GHCR 에서, 모델은 버킷에서 받는다. 앱 코드와 키는 여기 없다.
# 실습용 eta 인스턴스와 경로가 겹치지 않게 /srv/lol 을 쓴다.
set -euo pipefail
ARCH=$(uname -m)

# ── Docker + Compose 플러그인 (Amazon Linux 2023 의 docker 패키지에는 Compose 가 없다) ──
dnf install -y docker git libicu perl-Digest-SHA
systemctl enable --now docker
usermod -aG docker ec2-user
mkdir -p /usr/local/lib/docker/cli-plugins
curl -fsSL "https://github.com/docker/compose/releases/latest/download/docker-compose-linux-$ARCH" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

# ── 배포 폴더: 바라는 상태(.env)와 모델 판들 ──
install -d -o ec2-user -g ec2-user /srv/lol /srv/lol/models

# AWS CLI 는 Amazon Linux 2023 에 기본으로 있다. 버킷은 콘솔에서 이 인스턴스를 붙여(리소스 액세스) 키 없이 쓴다.
# GitHub 러너 등록(이름표 lightsail)은 등록 토큰이 1시간짜리라 여기서 하지 않고 브라우저 SSH 에서 한다(README 9절).
echo "launch-script done"
