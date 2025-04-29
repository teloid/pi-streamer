// static/browse.js

// --- Get References to Modal Elements ---
// Find the modal container itself
const modal = document.getElementById("textViewerModal");
// Find the element where the filename will be displayed
const modalTitle = document.getElementById("modalTitle");
// Find the <pre> tag where the file content will be shown
const textContentElement = document.getElementById("textContent");
// Find the close button within the modal header
const closeBtn = modal ? modal.querySelector(".modal-header .close-button") : null;

// --- Function to Open Modal and Fetch Text Content ---
function viewTextFile(url) {
    // Basic check to ensure modal elements exist in the DOM
    if (!modal || !modalTitle || !textContentElement) {
        console.error("Modal elements not found in the DOM!");
        alert("Error: Could not initialize the text file viewer.");
        return;
    }

    console.log("Attempting to fetch text content from URL:", url);

    // --- Prepare and Show Modal ---
    // Reset content and title to loading state
    textContentElement.textContent = "Loading file content...";
    textContentElement.scrollTop = 0; // Scroll to top
    modalTitle.textContent = "Loading...";
    modal.style.display = "block"; // Make the modal visible

    // --- Fetch Data from Server ---
    fetch(url)
        .then(response => {
            // Check if the HTTP response status is OK (e.g., 200)
            if (!response.ok) {
                // If not OK, try to parse the response body as JSON for an error message
                return response.json()
                    .then(errData => {
                        // If JSON parsing works and contains an error field, throw that error
                        throw new Error(errData.error || `Server error: ${response.status} ${response.statusText}`);
                    })
                    .catch(() => {
                        // If response wasn't JSON or parsing failed, throw a generic HTTP error
                        throw new Error(`Server error: ${response.status} ${response.statusText}`);
                    });
            }
            // If response is OK, parse it as JSON
            return response.json();
        })
        .then(data => {
            // --- Process Successful Response ---
            // Check if the parsed JSON data contains an error property (application-level error)
            if (data.error) {
                textContentElement.textContent = `Error loading file:\n\n${data.error}`;
                modalTitle.textContent = "Error";
                console.error("Server returned an application error:", data.error);
            } else if (data.content !== undefined) {
                // If content exists, display it
                textContentElement.textContent = data.content;
                modalTitle.textContent = data.filename || "Text File"; // Use filename from data or default
                console.log("Text content loaded successfully for:", data.filename);
            } else {
                 // Handle unexpected successful response without content
                 throw new Error("Received empty response from server.");
            }
        })
        .catch(error => {
            // --- Handle Fetch Errors or Thrown Errors ---
            console.error('Error fetching or processing text content:', error);
            // Display the error message in the modal
            textContentElement.textContent = `Failed to load content.\n\nError: ${error.message}`;
            modalTitle.textContent = "Loading Failed";
            // Optionally alert the user as well, though error is shown in modal
            // alert(`Failed to load text file: ${error.message}`);
        });
}

// --- Function to Close the Modal ---
function closeModal() {
    if (modal) {
        modal.style.display = "none"; // Hide the modal
        // Clear content and title for next time
        if (textContentElement) textContentElement.textContent = "";
        if (modalTitle) modalTitle.textContent = "";
        console.log("Text viewer modal closed.");
    }
}

// --- Event Listeners for Closing the Modal ---

// 1. Click on the close button (X)
if (closeBtn) {
    closeBtn.addEventListener('click', closeModal);
} else if (modal) {
     // Log error only if modal exists but button doesn't (shouldn't happen with correct HTML)
     console.warn("Modal close button not found.");
}


// 2. Click outside the modal content area (on the semi-transparent background)
if (modal) {
    window.addEventListener('click', function(event) {
        // If the direct target of the click is the modal background itself
        if (event.target === modal) {
            closeModal();
        }
    });
}

// 3. Press the Escape key
window.addEventListener('keydown', function (event) {
    // Check if the pressed key is Escape
    if (event.key === 'Escape' || event.key === 'Esc') {
        // Check if the modal is currently displayed
        if (modal && modal.style.display === "block") {
            closeModal();
        }
    }
});

// --- Initial Check ---
// (Optional) You could add a check here to ensure the modal exists on page load
if (!modal && document.querySelector('.item-action-button.view')) {
     console.warn("View buttons exist, but the textViewerModal element was not found. Text viewing will not work.");
} else {
     console.log("browse.js loaded successfully. Modal elements found.");
}