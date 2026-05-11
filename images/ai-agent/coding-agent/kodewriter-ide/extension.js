const vscode = require('vscode');

class KodewriterViewProvider {
    static viewType = 'kodewriter.agentView';

    constructor(extensionUri) {
        this.extensionUri = extensionUri;
    }

    resolveWebviewView(webviewView) {
        webviewView.webview.options = {
            enableScripts: true
        };

        webviewView.webview.html = this.getHtml();
    }

    getHtml() {
        return `
        <!DOCTYPE html>
        <html lang="en">
        <head>
            <meta charset="UTF-8">

            <meta
                http-equiv="Content-Security-Policy"
                content="
                    default-src 'none';
                    frame-src https://kodewriter.michaelhomelab.work;
                    style-src 'unsafe-inline';
                "
            />

            <style>
                html, body {
                    padding: 0;
                    margin: 0;
                    width: 100%;
                    height: 100%;
                    overflow: hidden;
                    background: #020617;
                }

                iframe {
                    width: 100%;
                    height: 100%;
                    border: none;
                }
            </style>
        </head>

        <body>
            <iframe
                src="https://kodewriter.michaelhomelab.work/agent/"
                allow="clipboard-read; clipboard-write">
            </iframe>
        </body>
        </html>
        `;
    }
}

function activate(context) {
    const provider = new KodewriterViewProvider(context.extensionUri);

    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider(
            KodewriterViewProvider.viewType,
            provider
        )
    );
}

function deactivate() { }

module.exports = {
    activate,
    deactivate
};