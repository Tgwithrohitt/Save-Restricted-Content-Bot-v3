"""Small health endpoint for the Save Post worker deployment."""
import os

from flask import Flask, jsonify, render_template

app = Flask(__name__)


@app.route("/")
def welcome():
    return render_template("welcome.html")


@app.route("/health")
def health():
    # Web-process liveness only; it does not imply Telegram or MongoDB readiness.
    return jsonify(status="ok", component="post-vault-web")


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
