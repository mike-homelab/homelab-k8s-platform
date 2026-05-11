FROM gitpod/openvscode-server:1.103.1

USER root
# Pre-install the Kodewriter extension into the global extensions directory
RUN mkdir -p /home/openvscode-server/.openvscode-server/extensions/kodewriter-sidebar
COPY package.json extension.js /home/openvscode-server/.openvscode-server/extensions/kodewriter-sidebar/

# Ensure correct ownership
RUN chown -R openvscode-server:openvscode-server /home/openvscode-server/.openvscode-server

USER openvscode-server
