/* Setup Wizard JavaScript */

let currentStep = 1;
const totalSteps = 2;

document.addEventListener('DOMContentLoaded', async () => {
    console.log('Setup wizard loaded');

    setupEventListeners();
    showStep(1);

    try {
        await api.post('/api/camera/start');
    } catch (error) {
        console.error('Camera start error:', error);
    }
});

function setupEventListeners() {
    const nextBtn = document.getElementById('nextBtn');
    const prevBtn = document.getElementById('prevBtn');
    const completeBtn = document.getElementById('completeBtn');

    nextBtn?.addEventListener('click', goToNextStep);
    prevBtn?.addEventListener('click', goToPreviousStep);
    completeBtn?.addEventListener('click', completeSetup);

    // Profile picture upload
    const profilePictureInput = document.getElementById('profilePicture');
    if (profilePictureInput) {
        profilePictureInput.addEventListener('change', (e) => {
            const file = e.target.files[0];
            if (file) {
                const reader = new FileReader();
                reader.onload = (event) => {
                    const preview = document.getElementById('profilePicturePreview');
                    preview.src = event.target.result;
                };
                reader.readAsDataURL(file);
            }
        });
    }
}

function showStep(step) {
    // Hide all steps
    document.querySelectorAll('.setup-content').forEach(el => {
        el.classList.remove('active');
    });

    // Show current step
    const stepEl = document.getElementById(`step${step}`);
    if (stepEl) {
        stepEl.classList.add('active');
    }

    // Update progress
    updateProgress(step);

    // Update buttons
    updateButtons(step);
}

function updateProgress(step) {
    document.querySelectorAll('.setup-step').forEach((el, index) => {
        el.classList.remove('active');
        if (index < step) {
            el.classList.add('active');
        }
    });
}

function updateButtons(step) {
    const nextBtn = document.getElementById('nextBtn');
    const prevBtn = document.getElementById('prevBtn');
    const completeBtn = document.getElementById('completeBtn');

    if (prevBtn) {
        prevBtn.style.display = step > 1 ? 'block' : 'none';
    }

    if (nextBtn && completeBtn) {
        if (step === totalSteps) {
            nextBtn.style.display = 'none';
            completeBtn.style.display = 'block';
        } else {
            nextBtn.style.display = 'block';
            completeBtn.style.display = 'none';
        }
    }
}

function goToNextStep() {
    if (validateStep(currentStep)) {
        if (currentStep < totalSteps) {
            currentStep++;
            showStep(currentStep);
        }
    } else {
        showToast('Please fill in all required fields', 'warning');
    }
}

function goToPreviousStep() {
    if (currentStep > 1) {
        currentStep--;
        showStep(currentStep);
    }
}

function validateStep(step) {
    switch (step) {
        case 1:
            const firstName = document.getElementById('firstName');
            const lastName = document.getElementById('lastName');
            return firstName?.value.trim() !== '' && lastName?.value.trim() !== '';
        case 2:
            return true; // Camera setup is optional
        case 3:
        case 4:
            return true;
        default:
            return true;
    }
}

async function completeSetup() {
    try {
        const firstName = document.getElementById('firstName')?.value || '';
        const lastName = document.getElementById('lastName')?.value || '';
        const userProfile = `${firstName.toLowerCase()}_${lastName.toLowerCase()}`;

        // Save settings
        await api.post('/api/settings/update', {
            user_profile: userProfile
        });

    showToast('Setup complete! Starting calibration...', 'success');

        setTimeout(() => {
            window.location.href = '/calibration';
        }, 2000);

    } catch (error) {
        console.error('Setup completion error:', error);
        showToast('Error completing setup', 'danger');
    }
}

// Keep camera running when navigating between app pages.
