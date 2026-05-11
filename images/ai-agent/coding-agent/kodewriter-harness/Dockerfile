FROM gitpod/openvscode-server:1.103.1

USER root

RUN mkdir -p /tmp/kodewriter

COPY package.json extension.js /tmp/kodewriter/
COPY media /tmp/kodewriter/media

RUN mkdir -p \
    /home/openvscode-server/.openvscode-server/extensions/kodewriter-sidebar

RUN cp -r /tmp/kodewriter/* \
    /home/openvscode-server/.openvscode-server/extensions/kodewriter-sidebar/

RUN chown -R openvscode-server:openvscode-server \
    /home/openvscode-server/.openvscode-server

USER openvscode-server