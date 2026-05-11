const vscode = require('vscode');

function activate(context) {
    const provider = {
        resolveWebviewView: (webviewView) => {
            webviewView.webview.options = { 
                enableScripts: true,
                localResourceRoots: [context.extensionUri]
            };
            webviewView.webview.html = `
                <!DOCTYPE html>
                <html lang="en" style="height: 100%; width: 100%;">
                <head>
                    <meta charset="UTF-8">
                    <meta name="viewport" content="width=device-width, initial-scale=1.0">
                    <title>Kodewriter Agent</title>
                    <style>
                        body, html, iframe { 
                            margin: 0; padding: 0; height: 100%; width: 100%; 
                            overflow: hidden; background-color: #020617;
                        }
                    </style>
                </head>
                <body>
                    <iframe src="https://kodewriter.michaelhomelab.work/agent/" 
                            style="width: 100%; height: 100%; border: none;"
                            allow="clipboard-read; clipboard-write;"></iframe>
                </body>
                </html>`;
        }
    };
    context.subscriptions.push(
        vscode.window.registerWebviewViewProvider('kodewriter.agentView', provider)
    );
}

function deactivate() {}

module.exports = {
    activate,
    deactivate
};
