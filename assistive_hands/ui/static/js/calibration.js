// assistive_hands/ui/static/js/calibration.js

/* Calibration JavaScript */

let calibrationPoints = [];
let currentPointIndex = 0;
let gazeBuffer = [];
let calibrationActive = false;
let calibrationPaused = false;
let activeSampleInterval = null;
let calibrationReferenceSize = { width: 1920, height: 1080 };
const canvas = document.getElementById('calibrationCanvas');
const ctx = canvas?.getContext('2d');

let dwellTime = 2.0;
let pointSize = 40;
let sensitivity = 1;

// Face detection tracking
let faceDetected = false;
let faceQuality = 0;
let eyeOpenness = 0;
let faceDetectionCheckInterval = null;

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Calibration page loaded');

    // Set canvas size AFTER layout so offsetWidth/Height are non-zero
    requestAnimationFrame(() => {
        setupCanvasSize();
    });
    setupEventListeners();
    
    // Camera is already running from Flask startup
    console.log('Setting up calibration...');
    try {
        showToast('Camera is running, please wait...', 'info');
        
        // Camera feed is already loaded from HTML template
        const cameraFeed = document.getElementById('calibrationCameraFeed');
        if (cameraFeed) {
            console.log('Camera feed element found and ready for MJPEG streaming');
            // Camera stream is continuous via src attribute - do not modify it
        } else {
            console.error('Camera feed element not found!');
            showToast('❌ Camera element not found in page', 'danger');
        }
        
        // Wait for camera warm-up, then show initial screen
        console.log('Waiting for camera warm-up (1 second)...');
        setTimeout(() => {
            drawInitialScreen();
            startFaceDetectionCheck();
            showToast('Position your face in front of camera', 'info');
        }, 1000);
        
    } catch (error) {
        console.error('Setup error:', error);
        showToast('❌ Setup error: ' + error.message, 'danger');
        drawInitialScreen();
        
        // Show retry button
        const retryBtn = document.createElement('button');
        retryBtn.className = 'btn btn-warning btn-lg mt-3';
        retryBtn.innerHTML = '<i class="fas fa-sync"></i> Retry Camera';
        retryBtn.onclick = () => location.reload();
        document.querySelector('.calibration-container')?.appendChild(retryBtn);
    }
});

function startFaceDetectionCheck() {
    console.log('Starting face detection check...');
    let checkCount = 0;
    
    // Check face detection every 500ms
    faceDetectionCheckInterval = setInterval(async () => {
        try {
            checkCount++;
            const response = await api.get('/api/gaze/current');
            
            if (response && response.status === 'success') {
                faceDetected = response.face_detected || false;
                eyeOpenness = response.eye_openness || 0;
                
                if (checkCount % 4 === 0) {  // Log every 2 seconds
                    console.log(`Face detection check #${checkCount}: detected=${faceDetected}, eyes=${(eyeOpenness*100).toFixed(0)}%`);
                }
                
                // Face quality: must be detected and eyes must be reasonably open
                if (faceDetected && eyeOpenness > 0.1) {
                    faceQuality = Math.min(1.0, eyeOpenness);
                } else {
                    faceQuality = 0;
                }
                
                updateFaceDetectionUI();
            } else {
                console.warn('Gaze API response error:', response);
            }
        } catch (error) {
            console.debug('Face detection check error:', error);
        }
    }, 500);
}

function updateFaceDetectionUI() {
    const startBtn = document.getElementById('startCalibrationBtn');
    const statusDiv = document.getElementById('faceDetectionStatus') || createFaceStatusDisplay();
    
    if (faceDetected && faceQuality > 0.4) {
        statusDiv.className = 'calibration-face-status is-good';
        statusDiv.innerHTML = `<strong>Face Detected</strong><span>Eyes Open: ${(eyeOpenness * 100).toFixed(0)}%</span>`;
        updateCalibrationState('waiting', 'Ready to calibrate', 'Start when your face is centered.');
        startBtn.disabled = false;
    } else if (faceDetected) {
        statusDiv.className = 'calibration-face-status is-warning';
        statusDiv.innerHTML = `<strong>Face Detected (Low Quality)</strong><span>Eyes Open: ${(eyeOpenness * 100).toFixed(0)}%. Open eyes wider.</span>`;
        updateCalibrationState('waiting', 'Waiting for better face tracking', 'Open your eyes wider and improve lighting.');
        startBtn.disabled = true;
    } else {
        statusDiv.className = 'calibration-face-status is-error';
        statusDiv.innerHTML = '<strong>No Face Detected</strong><span>Move closer to camera and ensure good lighting.</span>';
        updateCalibrationState('waiting', 'Waiting for face', 'Center your face and keep your eyes open.');
        startBtn.disabled = true;
    }
}

function createFaceStatusDisplay() {
    const statusDiv = document.createElement('div');
    statusDiv.id = 'faceDetectionStatus';
    statusDiv.className = 'calibration-face-status';
    
    const container = document.querySelector('.calibration-container') || document.body;
    container.appendChild(statusDiv);
    return statusDiv;
}

function setupCanvasSize() {
    if (!canvas) return;
    // Use offsetWidth/Height — must be called after layout
    const w = canvas.offsetWidth || window.innerWidth;
    const h = canvas.offsetHeight || window.innerHeight * 0.75;
    canvas.width  = w;
    canvas.height = h;
    console.log(`Canvas sized to ${w}x${h}`);
    if (ctx) ctx.clearRect(0, 0, w, h);
}

function setupEventListeners() {
    const startBtn = document.getElementById('startCalibrationBtn');
    const pauseBtn = document.getElementById('pauseCalibrationBtn');
    const cancelBtn = document.getElementById('cancelCalibrationBtn');

    // Initially disable start button (until face is detected)
    if (startBtn) {
        startBtn.disabled = true;
    }

    const sensitivitySlider = document.getElementById('sensitivitySlider');
    const dwellTimeSlider = document.getElementById('dwellTimeSlider');
    const pointSizeSlider = document.getElementById('pointSizeSlider');

    startBtn?.addEventListener('click', startCalibration);
    pauseBtn?.addEventListener('click', pauseCalibration);
    cancelBtn?.addEventListener('click', cancelCalibration);

    sensitivitySlider?.addEventListener('input', (e) => {
        sensitivity = parseFloat(e.target.value);
    });

    dwellTimeSlider?.addEventListener('input', (e) => {
        dwellTime = parseFloat(e.target.value);
        updateSliderDisplay();
    });

    pointSizeSlider?.addEventListener('input', (e) => {
        pointSize = parseInt(e.target.value);
    });

    window.addEventListener('resize', setupCanvasSize);
}

function drawInitialScreen() {
    if (!ctx || !canvas) return;

    // Keep canvas transparent - don't fill with black
    // Let the camera feed show through
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    
    // Update status message instead
    const statusEl = document.getElementById('calibrationStatus');
    if (statusEl) {
        statusEl.innerHTML = `
            <div class="calibration-stage-message">
                <h3>Gaze Calibration</h3>
                <p>Position your face in front of camera</p>
                <p>Camera feed should be visible behind this message</p>
            </div>
        `;
    }
    updateCalibrationState('waiting', 'Waiting for face', 'Center your face and keep your eyes open.');
}

async function startCalibration() {
    // Validate face is still properly detected
    if (!faceDetected || faceQuality < 0.4) {
        showToast('Please position your face properly in camera', 'warning');
        return;
    }
    
    try {
        const response = await api.post('/api/calibration/start');
        
        if (response.status === 'success') {
            calibrationReferenceSize = response.screen || calibrationReferenceSize;
            // Stop face detection check during calibration
            if (faceDetectionCheckInterval) {
                clearInterval(faceDetectionCheckInterval);
                faceDetectionCheckInterval = null;
            }
            
            // Hide face detection status and calibration instructions
            const statusDiv = document.getElementById('faceDetectionStatus');
            if (statusDiv) statusDiv.style.display = 'none';
            
            const calibrationStatus = document.getElementById('calibrationStatus');
            if (calibrationStatus) calibrationStatus.style.display = 'none';
            
            calibrationPoints = response.points;
            currentPointIndex = 0;
            calibrationActive = true;
            calibrationPaused = false;
            gazeBuffer = [];

            document.getElementById('startCalibrationBtn').classList.add('setup-hidden');
            document.getElementById('pauseCalibrationBtn').classList.remove('setup-hidden');

            updateCalibrationState('running', 'Calibration running', `Point 1 of ${calibrationPoints.length}. Follow each target.`);
            showToast('Calibration started - Follow the points with your eyes', 'success');
            startNextPoint();
        }
    } catch (error) {
        console.error('Calibration start error:', error);
        showToast('Failed to start calibration', 'danger');
    }
}

async function startNextPoint() {
    if (currentPointIndex >= calibrationPoints.length) {
        await finishCalibration();
        return;
    }

    const point = calibrationPoints[currentPointIndex];
    gazeBuffer = [];

    // Update progress
    updateProgress();

    // Draw current point
    drawCalibrationPoint(point);
    
    // Draw face detection status in corner
    drawFaceDetectionStatus();

    // Update status
    const statusEl = document.getElementById('statusMessage');
    if (statusEl) {
        statusEl.textContent = `Focus on point ${currentPointIndex + 1} of ${calibrationPoints.length}`;
    }
    updateCalibrationState('running', 'Calibration running', `Focus on point ${currentPointIndex + 1} of ${calibrationPoints.length}.`);

    // Start collecting gaze samples
    let elapsedTime = 0;
    clearActiveSampleInterval();
    activeSampleInterval = setInterval(async () => {
        if (!calibrationActive) {
            clearActiveSampleInterval();
            return;
        }
        if (calibrationPaused) {
            drawCalibrationPoint(point);
            drawFaceDetectionStatus();
            return;
        }

        try {
            const response = await api.get('/api/gaze/current');
            if (response.status === 'success') {
                const gaze = response.gaze_normalized;
                gazeBuffer.push([gaze.x, gaze.y]);
            }
        } catch (error) {
            console.error('Gaze sampling error:', error);
        }

        elapsedTime += 0.1;

        // Draw progress circle and redraw point
        drawProgressCircle(point, elapsedTime, dwellTime);
        
        // Keep face detection indicator visible
        drawFaceDetectionStatus();

        if (elapsedTime >= dwellTime) {
            clearActiveSampleInterval();
            
            // Submit calibration point
            await submitCalibrationPoint(point);

            currentPointIndex++;
            setTimeout(startNextPoint, 500);
        }
    }, 100);
}

function scalePoint(point) {
    if (!canvas || !Array.isArray(point)) return [0, 0];

    const rawX = Number(point[0]);
    const rawY = Number(point[1]);

    if (rawX >= 0 && rawX <= 1 && rawY >= 0 && rawY <= 1) {
        return [rawX * canvas.width, rawY * canvas.height];
    }

    const refWidth = calibrationReferenceSize?.width || canvas.width;
    const refHeight = calibrationReferenceSize?.height || canvas.height;
    return [
        rawX * (canvas.width / refWidth),
        rawY * (canvas.height / refHeight)
    ];
}

function drawCalibrationPoint(point) {
    if (!ctx || !canvas) return;
    const [x, y] = scalePoint(point);

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Outer green circle
    ctx.fillStyle = '#00FF00';
    ctx.beginPath();
    ctx.arc(x, y, pointSize, 0, 2 * Math.PI);
    ctx.fill();

    ctx.strokeStyle = '#FFFFFF';
    ctx.lineWidth = 3;
    ctx.stroke();

    // Center white dot
    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath();
    ctx.arc(x, y, pointSize / 3, 0, 2 * Math.PI);
    ctx.fill();
}

function drawProgressCircle(point, elapsed, duration) {
    if (!ctx || !canvas) return;
    const [x, y] = scalePoint(point);
    const progress = Math.min(1, elapsed / duration);

    ctx.fillStyle = '#00FF00';
    ctx.beginPath();
    ctx.arc(x, y, pointSize, 0, 2 * Math.PI);
    ctx.fill();

    ctx.strokeStyle = '#FFFFFF';
    ctx.lineWidth = 3;
    ctx.stroke();

    ctx.fillStyle = '#FFFFFF';
    ctx.beginPath();
    ctx.arc(x, y, pointSize / 3, 0, 2 * Math.PI);
    ctx.fill();

    // Progress ring
    ctx.strokeStyle = 'rgba(0,255,0,0.8)';
    ctx.lineWidth = 5;
    ctx.beginPath();
    ctx.arc(x, y, pointSize + 18, -Math.PI/2, -Math.PI/2 + 2 * Math.PI * progress);
    ctx.stroke();

    const pct = Math.round(progress * 100);
    ctx.fillStyle = '#FFFF00';
    ctx.font = 'bold 16px Arial';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'top';
    ctx.fillText(pct + '%', x, y + pointSize + 12);
}

function drawFaceDetectionStatus() {
    if (!ctx || !canvas) return;
    
    // Draw status indicator in top-left corner
    const padding = 15;
    const boxWidth = 200;
    const boxHeight = 60;
    
    // Draw semi-transparent background
    ctx.fillStyle = 'rgba(0, 0, 0, 0.6)';
    ctx.fillRect(padding, padding, boxWidth, boxHeight);
    
    // Draw border
    ctx.strokeStyle = faceDetected ? '#00FF00' : '#FF0000';
    ctx.lineWidth = 2;
    ctx.strokeRect(padding, padding, boxWidth, boxHeight);
    
    // Draw status text
    ctx.fillStyle = faceDetected ? '#00FF00' : '#FF0000';
    ctx.font = 'bold 14px Arial';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'top';
    ctx.fillText(faceDetected ? '✓ Face Detected' : '✗ No Face', padding + 10, padding + 8);
    
    // Draw eye openness bar
    ctx.fillStyle = '#666';
    ctx.fillRect(padding + 10, padding + 30, 180, 12);
    
    ctx.fillStyle = '#00FF00';
    ctx.fillRect(padding + 10, padding + 30, 180 * eyeOpenness, 12);
    
    ctx.fillStyle = '#FFF';
    ctx.font = '11px Arial';
    ctx.textAlign = 'center';
    ctx.fillText('Eyes: ' + (eyeOpenness * 100).toFixed(0) + '%', padding + 100, padding + 31);
}

async function submitCalibrationPoint(point) {
    try {
        const response = await api.post('/api/calibration/point', {
            target_point: point,
            gaze_samples: gazeBuffer
        });

        if (response.status === 'success') {
            console.log('Point recorded:', point);
        }
    } catch (error) {
        console.error('Error submitting calibration point:', error);
        showToast('Failed to record calibration point', 'warning');
    }
}

async function finishCalibration() {
    calibrationActive = false;

    try {
        console.log('Sending calibration calculate request...');
        const response = await api.post('/api/calibration/calculate');
        console.log('Calibration response:', response);

        if (response && response.status === 'success') {
            const isCalibrated = response.calibrated === true;
            const toastType = isCalibrated ? 'success' : 'warning';
            const toastMsg = isCalibrated ? 'Calibration successful!' : 'Calibration completed (lower accuracy)';
            
            showToast(toastMsg, toastType);
            updateCalibrationState('complete', 'Calibration complete', isCalibrated ? 'Returning to Dashboard.' : 'Completed with lower accuracy. Returning to Dashboard.');
            
            // Show validation metrics
            if (response.validation) {
                const meanError = response.validation.mean_error || 0;
                const maxError = response.validation.max_error || 0;
                console.log(`Validation - Mean Error: ${meanError.toFixed(2)}px, Max Error: ${maxError.toFixed(2)}px`);
            }
            
            // Draw success/completion screen
            if (ctx && canvas) {
                ctx.fillStyle = 'rgba(0, 0, 0, 0.8)';
                ctx.fillRect(0, 0, canvas.width, canvas.height);

                ctx.fillStyle = isCalibrated ? '#00FF00' : '#FFAA00';
                ctx.font = 'bold 48px Arial';
                ctx.textAlign = 'center';
                ctx.textBaseline = 'middle';
                ctx.fillText('✓ Calibration Complete', canvas.width / 2, canvas.height / 2 - 60);

                ctx.font = '24px Arial';
                ctx.fillStyle = '#FFFFFF';
                const accuracy = response.validation && response.validation.mean_error ? 
                    response.validation.mean_error.toFixed(2) : 'N/A';
                ctx.fillText(`Accuracy: ${accuracy}px`, canvas.width / 2, canvas.height / 2 + 40);
                
                if (!isCalibrated) {
                    ctx.fillStyle = '#FFAA00';
                    ctx.font = '18px Arial';
                    ctx.fillText('(Lower accuracy - may need recalibration)', canvas.width / 2, canvas.height / 2 + 100);
                }
            }

            setTimeout(() => {
                window.location.href = '/';
            }, 2000);
        } else {
            showToast(`Calibration error: ${response.message || 'Unknown error'}`, 'danger');
            updateCalibrationState('error', 'Calibration failed', response.message || 'Unknown calibration error.');
            resetCalibration();
        }
    } catch (error) {
        console.error('Calibration calculation error:', error);
        showToast('Error completing calibration: ' + error.message, 'danger');
        updateCalibrationState('error', 'Calibration failed', error.message);
        resetCalibration();
    }
}

function pauseCalibration() {
    if (!calibrationActive) return;
    calibrationPaused = !calibrationPaused;
    const pauseBtn = document.getElementById('pauseCalibrationBtn');
    if (pauseBtn) {
        pauseBtn.innerHTML = calibrationPaused ?
            '<i class="fas fa-play"></i> Resume' :
            '<i class="fas fa-pause"></i> Pause';
        pauseBtn.setAttribute('aria-pressed', calibrationPaused ? 'true' : 'false');
    }
    updateCalibrationState(
        calibrationPaused ? 'paused' : 'running',
        calibrationPaused ? 'Calibration paused' : 'Calibration running',
        calibrationPaused ? 'Resume when you are ready.' : `Focus on point ${currentPointIndex + 1} of ${calibrationPoints.length}.`
    );
}

async function cancelCalibration() {
    calibrationActive = false;
    calibrationPaused = false;
    clearActiveSampleInterval();
    resetCalibration();
    showToast('Calibration cancelled', 'info');
    try {
        await api.post('/api/calibration/cancel');
    } catch (error) {
        console.warn('Calibration cancel API unavailable:', error);
    }
    window.location.href = '/';
}

function resetCalibration() {
    currentPointIndex = 0;
    gazeBuffer = [];
    calibrationPoints = [];
    calibrationPaused = false;
    clearActiveSampleInterval();

    document.getElementById('startCalibrationBtn')?.classList.remove('setup-hidden');
    document.getElementById('pauseCalibrationBtn')?.classList.add('setup-hidden');

    // Restart face detection check
    if (!faceDetectionCheckInterval) {
        startFaceDetectionCheck();
    }
    
    // Show face detection status again
    const statusDiv = document.getElementById('faceDetectionStatus');
    if (statusDiv) statusDiv.style.display = 'block';

    updateProgress();
    drawInitialScreen();
}
function clearActiveSampleInterval() {
    if (activeSampleInterval) {
        clearInterval(activeSampleInterval);
        activeSampleInterval = null;
    }
}

function updateCalibrationState(state, label, message) {
    const stateCard = document.getElementById('calibrationStateCard');
    const labelEl = document.getElementById('calibrationStateLabel');
    const messageEl = document.getElementById('calibrationStateMessage');

    if (stateCard) {
        stateCard.className = `calibration-state-card mb-4 is-${state}`;
    }
    if (labelEl) {
        labelEl.textContent = label;
    }
    if (messageEl) {
        messageEl.textContent = message;
    }
}

function updateProgress() {
    const progressBar = document.getElementById('progressBar');
    const progressText = document.getElementById('progressText');

    if (progressBar && progressText) {
        const progress = (currentPointIndex / (calibrationPoints.length || 1)) * 100;
        progressBar.style.width = progress + '%';
        progressText.textContent = `${currentPointIndex}/${calibrationPoints.length || 9}`;
    }
}

function updateSliderDisplay() {
    const display = document.querySelector('[id="dwellTimeSlider"]')?.nextElementSibling;
    if (display) {
        display.textContent = dwellTime.toFixed(1) + 's';
    }
}

// Cleanup — do NOT stop camera on unload, it needs to stay alive for gaze tracking
window.addEventListener('beforeunload', () => {
    calibrationActive = false;
    clearActiveSampleInterval();
    if (faceDetectionCheckInterval) clearInterval(faceDetectionCheckInterval);
});
