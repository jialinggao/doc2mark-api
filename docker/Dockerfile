FROM python:3.11-slim

WORKDIR /app

# 切换 apt 源为清华源（Debian trixie DEB822 格式）
RUN printf 'Types: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian\nSuites: trixie trixie-updates trixie-backports\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n\nTypes: deb\nURIs: https://mirrors.tuna.tsinghua.edu.cn/debian-security\nSuites: trixie-security\nComponents: main contrib non-free non-free-firmware\nSigned-By: /usr/share/keyrings/debian-archive-keyring.gpg\n' > /etc/apt/sources.list.d/debian.sources

RUN apt update && apt install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    tesseract-ocr-eng \
    exiftool \
    poppler-utils \
    libreoffice \
    libreoffice-writer \
    libreoffice-base \
    libreoffice-calc \
    libreoffice-draw \
    libreoffice-impress \
    libreoffice-math \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

COPY app/ ./app/

RUN mkdir -p temp logs

EXPOSE 5926
