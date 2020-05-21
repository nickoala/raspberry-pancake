/*global $, WebSocket, console, window, document*/
"use strict";

/**
 * Connects to Pi server and receives video data.
 */
var client = {

    // Connects to Pi via websocket
    connect: function () {
        var self = this,
            video = document.getElementById("video"),
            fps = document.getElementById("fps"),
            lastFrameTime = Date.now(),
            fpsValue = 0;

        var host = window.location.href
                    .replace(/(^\w+:|^)\/\//, "")  // strip protocol
                    .replace(/\/$/, "");     // strip trailing slash

        // adapt to http or https
        var protocol = (window.location.protocol == "https:" ? "wss:" : "ws:");

        this.socket = new WebSocket(protocol + "//" + host + "/websocket");

        // Request the video stream once connected
        this.socket.onopen = function () {
            console.log("Connected!");
            self.readFrame();
            initFpsValue();
        };

        this.socket.onmessage = function (message) {
            var blob = new Blob([message.data], { type: "image/jpeg" });
            var url = window.URL || window.webkitURL;

            if (video.src) {
                var old = video.src;
                video.src = url.createObjectURL(blob);
                url.revokeObjectURL(old);
            }
            else {
                video.src = url.createObjectURL(blob);
            }

            updateFpsValue();
            self.readFrame();
        };

        setInterval(refreshFps, 1000);

        function initFpsValue () {
            lastFrameTime = Date.now();
            fpsValue = 0;
        }

        function updateFpsValue () {
            var now = Date.now();

            // Newest framerate, 1 frame / time between two frames (ms)
            var x = 1000.0 / (now - lastFrameTime);

            lastFrameTime = now;

            // Adjust the weights to vary smoothing
            // Ref:
            //   https://stackoverflow.com/questions/87304/calculating-frames-per-second-in-a-game
            fpsValue = (0.5 * x) + (0.5 * fpsValue);
        }

        function refreshFps () {
            fps.textContent = (fpsValue > 0.01) ?
                          "FPS " + fpsValue.toFixed(1) : "";
        }
    },

    // Requests video stream
    readFrame: function () {
        this.socket.send("read_frame");
    }
};
