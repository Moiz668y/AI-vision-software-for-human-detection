/**
 * Android Camera Management Dashboard
 * Handle connection, configuration, and streaming from Android devices
 * With keyboard shortcuts and gaze cursor tracking
 */

class AndroidCameraManager {
    constructor() {
        this.devices = {};
        this.primaryDevice = null;
        this.videoElement = null;
        this.gazeCursor = null;
        this.cursorX = 0;
        this.cursorY = 0;
        this.gazeEnabled = true;
        this.androidModeActive = false;
        this.pollingInterval = null;
        this.init();
    }

    async init() {
        this.setupVideoElement();
        this.setupEventListeners();
        this.setupKeyboardShortcuts();
        this.setupGazeCursor();
        this.androidModeActive = document.getElementById('android-camera-tab')?.classList.contains('active') || false;
        if (this.androidModeActive) {
            await this.refreshDeviceList();
            this.startStatusPolling();
        }
    }

    setupVideoElement() {
        // Create or get video element for Android feed
        let container = document.getElementById('androidCameraContainer');
        if (container) {
            this.videoElement = document.createElement('img');
            this.videoElement.id = 'androidVideoFeed';
            this.videoElement.className = 'img-fluid';
            this.videoElement.style.width = '100%';
            this.videoElement.style.height = 'auto';
            this.videoElement.alt = 'Android Camera Feed';
            
            // Clear container and add video element
            container.innerHTML = '';
            container.appendChild(this.videoElement);
        }
    }

    setupGazeCursor() {
        // Create gaze cursor overlay
        let cursorOverlay = document.createElement('div');
        cursorOverlay.id = 'androidGazeCursor';
        cursorOverlay.style.cssText = `
            position: fixed;
            width: 20px;
            height: 20px;
            background: radial-gradient(circle, rgba(255, 0, 0, 0.8), rgba(255, 0, 0, 0.3));
            border: 2px solid #ff0000;
            border-radius: 50%;
            pointer-events: none;
            display: none;
            z-index: 9999;
            box-shadow: 0 0 10px rgba(255, 0, 0, 0.5);
            transition: opacity 0.1s ease, box-shadow 0.1s ease;
        `;
        document.body.appendChild(cursorOverlay);
        this.gazeCursor = cursorOverlay;
    }

    setupKeyboardShortcuts() {
        document.addEventListener('keydown', (e) => {
            // Prevent shortcuts if user is typing
            if (document.activeElement.isContentEditable ||
                document.activeElement.tagName === 'INPUT' ||
                document.activeElement.tagName === 'TEXTAREA' ||
                document.activeElement.tagName === 'SELECT') {
                return;
            }

            switch(e.key.toUpperCase()) {
                case 'A': // Switch to Android camera
                    e.preventDefault();
                    this.switchToAndroidCamera();
                    break;
                default:
                    if (!this.androidModeActive) return;
                    break;
            }

            switch(e.key.toUpperCase()) {
                case 'D': // Switch to Desktop camera
                    e.preventDefault();
                    this.switchToDesktopCamera();
                    break;
                case 'R': // Refresh devices
                    e.preventDefault();
                    this.refreshDeviceList();
                    this.showNotification('Refreshing device list...', 'info');
                    break;
                case 'C': // Connect device
                    e.preventDefault();
                    this.showConnectionDialog();
                    break;
                case ' ': // Toggle gaze
                    e.preventDefault();
                    this.gazeEnabled = !this.gazeEnabled;
                    this.gazeCursor.style.display = this.gazeEnabled ? 'block' : 'none';
                    this.showNotification(
                        `Gaze ${this.gazeEnabled ? 'ENABLED' : 'DISABLED'}`,
                        this.gazeEnabled ? 'success' : 'warning'
                    );
                    break;
                case 'ESCAPE': // Emergency stop
                    e.preventDefault();
                    this.emergencyStop();
                    break;
                case 'M': // Use mouse
                    e.preventDefault();
                    this.toggleMouseControl();
                    break;
            }
        });

        // Track mouse movement for gaze cursor
        document.addEventListener('mousemove', (e) => {
            if (this.androidModeActive && this.gazeEnabled && this.gazeCursor) {
                this.cursorX = e.clientX;
                this.cursorY = e.clientY;
                this.gazeCursor.style.left = (e.clientX - 10) + 'px';
                this.gazeCursor.style.top = (e.clientY - 10) + 'px';
            }
        });
    }

    switchToAndroidCamera() {
        const tab = document.getElementById('android-camera-tab');
        if (tab) {
            const bsTab = new bootstrap.Tab(tab);
            bsTab.show();
            this.showNotification('Switched to Android Camera', 'info');
        }
    }

    switchToDesktopCamera() {
        const tab = document.getElementById('desktop-camera-tab');
        if (tab) {
            const bsTab = new bootstrap.Tab(tab);
            bsTab.show();
            this.showNotification('Switched to Desktop Camera', 'info');
        }
    }

    emergencyStop() {
        this.gazeEnabled = false;
        this.gazeCursor.style.display = 'none';
        this.disconnectAllDevices();
        this.showNotification('⚠️ EMERGENCY STOP - All devices disconnected', 'danger');
    }

    toggleMouseControl() {
        // Toggle between gaze control and mouse control
        this.gazeEnabled = !this.gazeEnabled;
        this.showNotification(
            `Switched to ${this.gazeEnabled ? 'GAZE' : 'MOUSE'} control`,
            'info'
        );
    }

    setupEventListeners() {
        document.getElementById('androidConnectBtn')?.addEventListener('click', () => {
            this.showConnectionDialog();
        });

        document.getElementById('refreshDevicesBtn')?.addEventListener('click', () => {
            this.refreshDeviceList();
        });

        document.getElementById('android-camera-tab')?.addEventListener('shown.bs.tab', async () => {
            this.androidModeActive = true;
            await this.refreshDeviceList();
            this.startStatusPolling();
        });

        document.getElementById('desktop-camera-tab')?.addEventListener('shown.bs.tab', () => {
            this.androidModeActive = false;
            this.stopStatusPolling();
            if (this.gazeCursor) {
                this.gazeCursor.style.display = 'none';
            }
        });
    }

    async listAvailableDevices() {
        try {
            const response = await axios.get('/api/android/devices');
            return response.data.devices || [];
        } catch (error) {
            console.error('Failed to list devices:', error);
            this.showNotification('Failed to list Android devices', 'error');
            return [];
        }
    }

    async getDeviceInfo(deviceId) {
        try {
            const response = await axios.get(`/api/android/device-info/${deviceId}`);
            return response.data.info || {};
        } catch (error) {
            console.error('Failed to get device info:', error);
            return {};
        }
    }

    async connectDevice(deviceId, options = {}) {
        const {
            deviceIp = null,
            port = 8888,
            mode = 'network',
            useAsPrimary = true
        } = options;

        try {
            const response = await axios.post('/api/android/connect', {
                device_id: deviceId,
                device_ip: deviceIp,
                port: port,
                mode: mode,
                use_as_primary: useAsPrimary
            });

            if (response.data.connected) {
                this.devices[deviceId] = {
                    id: deviceId,
                    ip: deviceIp,
                    port: port,
                    mode: mode,
                    connected: true,
                    isPrimary: useAsPrimary
                };

                if (useAsPrimary) {
                    this.primaryDevice = deviceId;
                    this.startPrimaryStream();
                }

                this.showNotification(`✓ Connected to ${deviceId}`, 'success');
                await this.refreshDeviceList();
            } else {
                this.showNotification(`✗ Failed to connect to ${deviceId}`, 'error');
            }
        } catch (error) {
            console.error('Connection failed:', error);
            this.showNotification('Connection failed', 'error');
        }
    }

    async disconnectDevice(deviceId) {
        try {
            await axios.post(`/api/android/disconnect/${deviceId}`);
            delete this.devices[deviceId];

            if (this.primaryDevice === deviceId) {
                this.primaryDevice = null;
                if (this.videoElement) {
                    this.videoElement.src = '';
                }
            }

            this.showNotification(`Disconnected from ${deviceId}`, 'info');
            await this.refreshDeviceList();
        } catch (error) {
            console.error('Disconnect failed:', error);
            this.showNotification('Disconnect failed', 'error');
        }
    }

    async disconnectAllDevices() {
        const deviceIds = Object.keys(this.devices);
        for (const deviceId of deviceIds) {
            await this.disconnectDevice(deviceId);
        }
    }

    async setPrimaryDevice(deviceId) {
        try {
            await axios.post(`/api/android/set-primary/${deviceId}`);
            this.primaryDevice = deviceId;
            this.startPrimaryStream();
            this.showNotification(`${deviceId} set as primary`, 'success');
            await this.refreshDeviceList();
        } catch (error) {
            console.error('Failed to set primary device:', error);
            this.showNotification('Failed to set primary device', 'error');
        }
    }

    async configureDevice(deviceId, config) {
        try {
            await axios.post('/api/android/configure', {
                device_id: deviceId,
                fps: config.fps,
                width: config.width,
                height: config.height
            });

            this.showNotification(`${deviceId} configured successfully`, 'success');
        } catch (error) {
            console.error('Configuration failed:', error);
            this.showNotification('Configuration failed', 'error');
        }
    }

    startPrimaryStream() {
        if (!this.primaryDevice || !this.videoElement) return;

        const streamUrl = `/api/android/primary-stream?ts=${Date.now()}`;
        this.videoElement.src = streamUrl;
        
        // Show loading state
        this.videoElement.style.opacity = '0.5';
        
        // Handle stream load
        this.videoElement.onload = () => {
            this.videoElement.style.opacity = '1';
        };

        this.videoElement.onerror = () => {
            console.error('Failed to load stream');
            this.showNotification('Failed to load Android stream', 'error');
            this.videoElement.style.opacity = '1';
        };
    }

    startStatusPolling() {
        if (!this.androidModeActive || this.pollingInterval) return;
        this.pollingInterval = setInterval(() => this.updateStatus(), 2000);
    }

    stopStatusPolling() {
        if (this.pollingInterval) {
            clearInterval(this.pollingInterval);
            this.pollingInterval = null;
        }
    }

    async updateStatus() {
        try {
            const response = await axios.get('/api/android/status');
            const devices = response.data.devices || {};

            Object.entries(devices).forEach(([deviceId, status]) => {
                const deviceEl = document.querySelector(`[data-device-id="${deviceId}"]`);
                if (deviceEl) {
                    const statusBadge = deviceEl.querySelector('.device-status');
                    if (statusBadge) {
                        statusBadge.textContent = status.connected ? 'Connected' : 'Disconnected';
                        statusBadge.className = `device-status badge ${status.connected ? 'bg-success' : 'bg-danger'}`;
                    }

                    const resolutionEl = deviceEl.querySelector('.device-resolution');
                    if (resolutionEl) {
                        resolutionEl.textContent = status.resolution;
                    }

                    const fpsEl = deviceEl.querySelector('.device-fps');
                    if (fpsEl) {
                        fpsEl.textContent = status.fps + ' fps';
                    }

                    if (status.is_primary) {
                        deviceEl.classList.add('active');
                    } else {
                        deviceEl.classList.remove('active');
                    }
                }
            });
        } catch (error) {
            console.error('Status update failed:', error);
        }
    }

    async refreshDeviceList() {
        try {
            const devices = await this.listAvailableDevices();
            const container = document.getElementById('androidDevicesList');

            if (!container) return;

            container.innerHTML = '';

            if (devices.length === 0) {
                container.innerHTML = `
                    <div class="alert alert-warning">
                        <strong>No Android devices found</strong><br>
                        <small>Make sure to install IP-Webcam on your Android device, or enable USB Debugging</small>
                    </div>
                `;
                return;
            }

            devices.forEach(deviceId => {
                const deviceEl = document.createElement('div');
                deviceEl.className = 'android-device-card card mb-3';
                deviceEl.dataset.deviceId = deviceId;
                deviceEl.innerHTML = `
                    <div class="card-body">
                        <div class="d-flex justify-content-between align-items-start mb-3">
                            <div>
                                <h6 class="device-name mb-1">📱 ${deviceId}</h6>
                                <small class="text-muted device-resolution">Resolution: -</small><br>
                                <small class="text-muted device-fps">FPS: -</small>
                            </div>
                            <span class="device-status badge bg-warning">Disconnected</span>
                        </div>
                        <div class="btn-group w-100" role="group">
                            <button class="btn btn-sm btn-success flex-grow-1" onclick="androidManager.connectDevice('${deviceId}', {useAsPrimary: true})">
                                <i class="fas fa-plug"></i> Connect
                            </button>
                            <button class="btn btn-sm btn-info flex-grow-1" onclick="androidManager.setPrimaryDevice('${deviceId}')">
                                <i class="fas fa-star"></i> Set Primary
                            </button>
                            <button class="btn btn-sm btn-danger flex-grow-1" onclick="androidManager.disconnectDevice('${deviceId}')">
                                <i class="fas fa-unplug"></i> Disconnect
                            </button>
                        </div>
                    </div>
                `;
                container.appendChild(deviceEl);
            });

            this.updateStatus();
        } catch (error) {
            console.error('Failed to refresh device list:', error);
        }
    }

    showConnectionDialog() {
        const modal = document.createElement('div');
        modal.className = 'modal fade';
        modal.id = 'connectionModal';
        modal.innerHTML = `
            <div class="modal-dialog">
                <div class="modal-content">
                    <div class="modal-header">
                        <h5 class="modal-title">Connect Android Device</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal"></button>
                    </div>
                    <div class="modal-body">
                        <div class="form-group mb-3">
                            <label for="connectionMode" class="form-label">Connection Mode:</label>
                            <select id="connectionMode" class="form-control">
                                <option value="network">📡 WiFi/Network (IP-Webcam)</option>
                                <option value="usb">🔌 USB (ADB)</option>
                            </select>
                            <small class="text-muted mt-2 d-block">
                                <strong>WiFi:</strong> Install "IP-Webcam" app from Play Store<br>
                                <strong>USB:</strong> Enable USB Debugging in Developer Options
                            </small>
                        </div>
                        <div class="form-group mb-3" id="ipGroup">
                            <label for="deviceIp" class="form-label">Device IP Address:</label>
                            <input type="text" id="deviceIp" class="form-control" placeholder="192.168.1.100">
                            <small class="text-muted mt-2 d-block">Enter the IP shown in IP-Webcam app</small>
                        </div>
                        <div class="form-group mb-3">
                            <label for="devicePort" class="form-label">Port:</label>
                            <input type="number" id="devicePort" class="form-control" value="8888" min="1024" max="65535">
                        </div>
                    </div>
                    <div class="modal-footer">
                        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">Cancel</button>
                        <button type="button" class="btn btn-primary" id="connectBtn" onclick="androidManager.handleConnect()">Connect</button>
                    </div>
                </div>
            </div>
        `;

        // Remove old modal if exists
        const oldModal = document.getElementById('connectionModal');
        if (oldModal) oldModal.remove();

        document.body.appendChild(modal);
        const bsModal = new bootstrap.Modal(modal);
        bsModal.show();
    }

    handleConnect() {
        const mode = document.getElementById('connectionMode').value;
        const deviceIp = document.getElementById('deviceIp').value;
        const devicePort = parseInt(document.getElementById('devicePort').value);

        if (mode === 'network' && !deviceIp) {
            this.showNotification('Please enter device IP address', 'error');
            return;
        }

        const deviceId = `${mode}_${deviceIp || 'usb'}_${devicePort}`;
        this.connectDevice(deviceId, {
            deviceIp: mode === 'network' ? deviceIp : null,
            port: devicePort,
            mode: mode,
            useAsPrimary: true
        });

        // Close modal
        const modal = bootstrap.Modal.getInstance(document.getElementById('connectionModal'));
        if (modal) modal.hide();
    }

    showNotification(message, type = 'info') {
        if (typeof showToast === 'function') {
            showToast(message, type === 'error' ? 'danger' : type, 5000);
            return;
        }
        console[type === 'error' ? 'error' : 'log'](message);
    }
}

// Initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        window.androidManager = new AndroidCameraManager();
    });
} else {
    window.androidManager = new AndroidCameraManager();
}
