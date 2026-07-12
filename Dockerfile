FROM ruby:3.4.10-slim-bookworm@sha256:6760b6e46941fb77f8229f52d1745a629a20f148c8685226d76758fcb6e33766 AS bundle-builder

ARG BUNDLER_VERSION=2.6.9
ARG DEBIAN_SNAPSHOT=20260712T043000Z

ENV BUNDLE_DEPLOYMENT=true \
    BUNDLE_PATH=/usr/local/bundle \
    DEBIAN_FRONTEND=noninteractive

WORKDIR /srv/jekyll

COPY Gemfile Gemfile.lock ./

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|g" \
        -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|g" \
        /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && \
    apt-get -y --no-install-recommends dist-upgrade && \
    apt-get install -y --no-install-recommends \
        build-essential \
        git \
        zlib1g-dev && \
    bundle _${BUNDLER_VERSION}_ --version && \
    bundle install --jobs 4 --retry 3 && \
    apt-get -s dist-upgrade > /tmp/apt-upgrade-plan && \
    ! grep -q '^Inst ' /tmp/apt-upgrade-plan && \
    rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/* /tmp/*

FROM node:24.18.0-bookworm-slim@sha256:cb4e8f7c443347358b7875e717c29e27bf9befc8f5a26cf18af3c3dec80e58c5 AS node-runtime
FROM python:3.13.14-slim-bookworm@sha256:fcbd8dfc2605ba7c2eca646846c5e892b2931e41f6227985154a596f26ab8ed7 AS python-runtime
FROM ruby:3.4.10-slim-bookworm@sha256:6760b6e46941fb77f8229f52d1745a629a20f148c8685226d76758fcb6e33766

ARG BUNDLER_VERSION=2.6.9
ARG CHROMIUM_VERSION=150.0.7871.114-1~deb12u1
ARG DEBIAN_SNAPSHOT=20260712T043000Z
ARG NODE_VERSION=24.18.0
ARG NPM_VERSION=11.18.0
ARG PYTHON_VERSION=3.13.14
ARG APP_GID=1000
ARG APP_UID=1000
ARG APP_USER=jekyll

ENV BUNDLE_DEPLOYMENT=true \
    DEBIAN_FRONTEND=noninteractive \
    EXECJS_RUNTIME=Node \
    JEKYLL_ENV=production \
    LANG=en_US.UTF-8 \
    LANGUAGE=en_US:en \
    LC_ALL=en_US.UTF-8

LABEL authors="Amir Pourmand,George Araújo" \
      description="Docker image for al-folio academic template" \
      maintainer="Amir Pourmand"

# Node and Python are copied from exact runtime images; Python 3.13 is supported
# by the canonical RenderCV 2.8 validator and renderer.
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=python-runtime /usr/local /usr/local

RUN sed -i \
        -e "s|http://deb.debian.org/debian-security|https://snapshot.debian.org/archive/debian-security/${DEBIAN_SNAPSHOT}|g" \
        -e "s|http://deb.debian.org/debian|https://snapshot.debian.org/archive/debian/${DEBIAN_SNAPSHOT}|g" \
        /etc/apt/sources.list.d/debian.sources && \
    printf 'Acquire::Check-Valid-Until "false";\n' > /etc/apt/apt.conf.d/99snapshot && \
    apt-get update && \
    apt-get -y --no-install-recommends dist-upgrade && \
    apt-get install -y --no-install-recommends \
        chromium="${CHROMIUM_VERSION}" \
        git \
        imagemagick \
        locales \
        poppler-utils && \
    bundle _${BUNDLER_VERSION}_ --version && \
    sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen && \
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

RUN groupadd --gid "${APP_GID}" "${APP_USER}" && \
    useradd --uid "${APP_UID}" --gid "${APP_GID}" --create-home --shell /bin/bash "${APP_USER}"

COPY requirements-build.txt /tmp/requirements-build.txt
RUN test "$(python3 --version)" = "Python ${PYTHON_VERSION}" && \
    test "$(node --version)" = "v${NODE_VERSION}" && \
    python3 -m pip install --no-cache-dir --break-system-packages --require-hashes -r /tmp/requirements-build.txt && \
    rm /tmp/requirements-build.txt

ENV HOME=/home/jekyll \
    BUNDLE_PATH=/usr/local/bundle

WORKDIR /srv/jekyll

# Keep dependency installation cacheable and fail if the manifest and lock drift.
COPY Gemfile Gemfile.lock ./
COPY --from=bundle-builder /usr/local/bundle /usr/local/bundle
RUN bundle _${BUNDLER_VERSION}_ check && \
    chown -R jekyll:jekyll /usr/local/bundle /home/jekyll /srv/jekyll

COPY --chmod=0755 bin/entry_point.sh /usr/local/bin/entry_point.sh

EXPOSE 8080 35729

USER jekyll

CMD ["/usr/local/bin/entry_point.sh"]
