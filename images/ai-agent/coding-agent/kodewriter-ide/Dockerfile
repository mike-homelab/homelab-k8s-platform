FROM gitpod/openvscode-server:1.103.1

USER root

ENV VSCODE_EXTENSIONS=/home/workspace/.openvscode-server/extensions

RUN mkdir -p ${VSCODE_EXTENSIONS}/kodewriter-sidebar

COPY package.json extension.js \
     ${VSCODE_EXTENSIONS}/kodewriter-sidebar/

COPY media \
     ${VSCODE_EXTENSIONS}/kodewriter-sidebar/media

RUN chown -R 1000:1000 /home/workspace/.openvscode-server

USER 1000