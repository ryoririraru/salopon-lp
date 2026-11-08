#!/usr/bin/env bash
# リール生成に必要なツールをインストール（Debian/Ubuntu）
set -e
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ffmpeg fonts-noto-cjk open-jtalk open-jtalk-mecab-naist-jdic
pip install -q Pillow
