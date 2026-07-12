FROM node:24.18.0-bookworm-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 AS node-runtime
FROM python:3.13.14-slim-bookworm@sha256:fcbd8dfc2605ba7c2eca646846c5e892b2931e41f6227985154a596f26ab8ed7 AS python-runtime
FROM ruby:3.4.10-slim-bookworm@sha256:6760b6e46941fb77f8229f52d1745a629a20f148c8685226d76758fcb6e33766 AS ruby-runtime
FROM mcr.microsoft.com/devcontainers/jekyll:3.4-bookworm@sha256:6866df1504c0f3450dd1569e9f9a30e80d9f0a26a3e047a7a7e479f6c6376f0b

RUN rm -rf /usr/local/lib/ruby /usr/local/rvm

ARG DEBIAN_SNAPSHOT=20260712T043000Z
ARG BUNDLER_VERSION=2.6.9
ARG NODE_VERSION=24.18.0
ARG NPM_VERSION=11.18.0
ARG RUBY_VERSION=3.4.10

COPY --from=ruby-runtime /usr/local /usr/local
COPY --from=python-runtime /usr/local /usr/local
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node

ENV PATH=/usr/local/bundle/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin

ENV BUNDLER_VERSION=${BUNDLER_VERSION} \
    GEM_HOME=/usr/local/bundle \
    GEM_PATH=/usr/local/bundle \
    MY_RUBY_HOME=/usr/local \
    RUBY_VERSION=${RUBY_VERSION} \
    RUBY_DOWNLOAD_URL=https://cache.ruby-lang.org/pub/ruby/3.4/ruby-3.4.10.tar.xz \
    RUBY_DOWNLOAD_SHA256=6f32ad662baafc228d12030dbcd284f83b034dd4337b300dc84ac74d11a1eb68

RUN rm -f /etc/apt/sources.list.d/yarn.list && \
    sed -i \
        -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|g" \
        -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|g" \
        /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && \
    apt-get -y --no-install-recommends dist-upgrade && \
    apt-get install -y --no-install-recommends \
        build-essential \
        imagemagick \
        lsof \
        poppler-utils \
        procps \
        zlib1g-dev && \
    bundle _${BUNDLER_VERSION}_ --version && \
    sed -i \
        's|^export PATH=.*|export PATH=/usr/local/bundle/bin:/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin|' \
        /etc/profile.d/00-restore-env.sh && \
    test "$(ruby --disable-gems -e 'print RUBY_VERSION')" = "${RUBY_VERSION}" && \
    test "$(node --version)" = "v${NODE_VERSION}" && \
    chown -R vscode:vscode /usr/local/bundle && \
    apt-get -s dist-upgrade > /tmp/apt-upgrade-plan && \
    ! grep -q '^Inst ' /tmp/apt-upgrade-plan && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

ADD --checksum=sha256:73f6155215ebabf4ed96dca1f567c2372cc713c33af2e5b9b62fde4e92373e2e https://registry.npmjs.org/npm/-/npm-11.18.0.tgz /tmp/npm.tgz
RUN rm -rf /usr/local/lib/node_modules/npm && \
    mkdir -p /usr/local/lib/node_modules/npm && \
    tar -xzf /tmp/npm.tgz --strip-components=1 -C /usr/local/lib/node_modules/npm && \
    ln -sfn ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sfn ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    test "$(npm --version)" = "${NPM_VERSION}" && \
    test "$(node -p 'require("/usr/local/lib/node_modules/npm/node_modules/undici/package.json").version')" = "6.27.0" && \
    rm /tmp/npm.tgz

# The MCR base contributes a network-running postCreate hook through this
# image label. Project-owned devcontainer.json is the sole lifecycle source.
LABEL devcontainer.metadata="[]"
