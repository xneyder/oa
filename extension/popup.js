document.getElementById('playButton').addEventListener('click', () => {
    // Send a message to the content script to start processing
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      const activeTab = tabs[0];
      chrome.scripting.executeScript(
        {
          target: { tabId: activeTab.id },
          files: ['content.js']
        },
        () => {
          // Execution complete
          document.getElementById('status').innerText = 'Processing...';
        }
      );
    });
});
