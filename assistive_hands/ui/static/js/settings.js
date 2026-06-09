/* Settings JavaScript */

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Settings page loaded');

    loadSettings();
    setupEventListeners();
    showSettingsSection('#cameraSection', document.querySelector('.settings-nav .list-group-item'));
});

function loadSettings() {
    api.get('/api/settings/get')
        .then(response => {
            if (response.status === 'success') {
                const settings = response.settings;
                applySettingsToUI(settings);
            }
        })
        .catch(error => {
            console.error('Error loading settings:', error);
            showToast('Failed to load settings', 'warning');
        });
}

function applySettingsToUI(settings) {
    // Camera settings
    const resolutionSelect = document.getElementById('resolution');
    if (resolutionSelect && settings.screen_resolution) {
        resolutionSelect.value = `${settings.screen_resolution[0]}x${settings.screen_resolution[1]}`;
    }

    // Dwell time
    const dwellInput = document.getElementById('dwellTime');
    if (dwellInput && settings.dwell_time) {
        dwellInput.value = settings.dwell_time;
    }

    // Button size
    const buttonSizeInputs = document.querySelectorAll('[id*="Size"]');
    if (settings.button_size) {
        buttonSizeInputs.forEach(input => {
            if (input.id === 'displaySize') {
                input.value = settings.button_size[0] > 60 ? 'Large' : 'Normal';
            }
        });
    }
}

function setupEventListeners() {
    const saveBtn = document.getElementById('saveSettingsBtn');
    const resetBtn = document.getElementById('resetSettingsBtn');

    // Tab navigation
    document.querySelectorAll('.settings-nav .list-group-item').forEach(item => {
        item.addEventListener('click', (e) => {
            e.preventDefault();
            const link = e.currentTarget;
            showSettingsSection(link.getAttribute('href'), link);
        });
    });

    saveBtn?.addEventListener('click', saveSettings);
    resetBtn?.addEventListener('click', resetSettings);

    // Dark mode toggle
    const darkModeToggle = document.getElementById('darkMode');
    darkModeToggle?.addEventListener('change', (e) => {
        document.body.classList.toggle('dark-mode', e.target.checked);
    });

    // High contrast toggle
    const highContrastToggle = document.getElementById('highContrast');
    highContrastToggle?.addEventListener('change', (e) => {
        document.body.classList.toggle('high-contrast', e.target.checked);
    });

    // Slider change events
    const sliders = document.querySelectorAll('input[type="range"]');
    sliders.forEach(slider => {
        slider.addEventListener('input', (e) => {
            const label = e.target.previousElementSibling;
            if (label) {
                const value = e.target.value;
                const unit = e.target.id.includes('brightness') || e.target.id.includes('contrast') ? 
                    '%' : e.target.id.includes('Size') ? 'px' : '';
                label.textContent = label.textContent.split(':')[0] + ': ' + value + unit;
            }
        });
    });
}

function showSettingsSection(sectionId, activeItem = null) {
    // Hide all sections
    document.querySelectorAll('.settings-section').forEach(section => {
        section.style.display = 'none';
    });

    // Show selected section
    const section = document.querySelector(sectionId);
    if (section) {
        section.style.display = 'block';
    }

    // Update active state
    document.querySelectorAll('.settings-nav .list-group-item').forEach(item => {
        item.classList.remove('active');
    });
    activeItem?.classList.add('active');
}

async function saveSettings() {
    try {
        const settings = {
            dwell_time: parseFloat(document.getElementById('dwellTime')?.value || 1.0),
            brightness: parseInt(document.getElementById('brightness')?.value || 0),
            contrast: parseInt(document.getElementById('contrast')?.value || 0),
            smoothing: parseInt(document.getElementById('smoothing')?.value || 50),
            sensitivity: parseFloat(document.getElementById('sensitivity')?.value || 1),
            resolution: document.getElementById('resolution')?.value,
            volume: parseInt(document.getElementById('volume')?.value || 70),
            display_size: document.getElementById('displaySize')?.value,
            dark_mode: document.getElementById('darkMode')?.checked || false,
            high_contrast: document.getElementById('highContrast')?.checked || false,
            performance_mode: document.getElementById('performanceMode')?.checked || false
        };

        const response = await api.post('/api/settings/update', settings);

        if (response.status === 'success') {
            showToast('Settings saved successfully', 'success');
        } else {
            showToast('Failed to save settings', 'danger');
        }
    } catch (error) {
        console.error('Error saving settings:', error);
        showToast('Error saving settings', 'danger');
    }
}

function resetSettings() {
    document.getElementById('dwellTime').value = '1.0';
    document.getElementById('brightness').value = '0';
    document.getElementById('contrast').value = '0';
    document.getElementById('smoothing').value = '50';
    document.getElementById('sensitivity').value = '1';
    document.getElementById('volume').value = '70';
    document.getElementById('darkMode').checked = false;
    document.getElementById('highContrast').checked = false;
    document.getElementById('performanceMode').checked = false;

    document.body.classList.remove('dark-mode', 'high-contrast');
    showToast('Settings reset to defaults', 'info');
    saveSettings();
}
