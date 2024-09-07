// Script Version: v1.5.3
// Author: Quentin King 
// Last Updated: 09-07-2024
// Description: Processes audio transcription payload, validates addresses, and sends enhanced messages with time, location, and call type. Now includes proper handling for Pushover API tokens and keys.
// Version Changelog:
// - v1.5.3 (09-07-2024): Fixed the issue with missing Pushover API token and user key from the global context.
// - v1.5.2 (09-07-2024): Fixed the issue with Google Maps URL not being set correctly. Updated fallback mechanism to ensure audio URL is used when address validation fails.

const moment = global.get('moment');
const axios = global.get('axios');

// Define response codes to match after 'Medical'
let responseCodes = ["Alpha", "Bravo", "Charlie", "Delta", "Echo"];
let callTypes = ["Cardiac Arrest", "Chest Pain", "Respiratory Distress", "Unconscious", "Seizure", "Trauma", "Stroke", "Breathing Problem", "Allergic Reaction", "Fall Injury"];

// Extract details from the payload
let department = msg.payload.msg.title.split(" ")[0]; // Extract the department from the title
let message = msg.payload.msg.payload;
let audioUrl = msg.payload.msg.url;
let audioFileName = msg.payload.msg.url_title;

// Initialize title and fallback
let title = `${department} Audio Transcribed`;

// Step 1: Find the medical response code
let medicalMatch = message.match(/\bmedical\s+(\w+)\b/i);
if (medicalMatch && responseCodes.includes(medicalMatch[1])) {
    let responseCode = medicalMatch[1];
    title = `${department} Medical ${responseCode} Response`;
} else {
    node.warn("No valid medical response code found, falling back to general title.");
}

// Step 2: Extract the call type
let callTypeMatch = callTypes.find(type => message.includes(type)) || "General Emergency";

// Step 3: Extract the address from the message
let addressPattern = /\d+\s[A-Za-z0-9\s]+(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Drive|Dr|Lane|Ln|Way|Court|Ct)[^,.]*/;
let addressMatch = message.match(addressPattern);
let address = addressMatch ? addressMatch[0] : null;

// Handle address and fix hyphenated numbers in addresses (e.g., "103-80" -> "10380")
if (address) {
    address = address.replace(/(\d+)-(\d+)/g, '$1$2'); // Remove hyphen between numbers
} else {
    node.warn("No address found in the message.");
}

// Step 4: Use Google Geolocation API to validate the address
if (address) {
    const googleApiKey = global.get('googleApiKey');

    const googleMapsApiUrl = `https://maps.googleapis.com/maps/api/geocode/json?address=${encodeURIComponent(address)}&components=administrative_area:NE|country:US&key=${googleApiKey}`;

    axios.get(googleMapsApiUrl)
        .then(response => {
            if (response.data.results && response.data.results.length > 0) {
                let validatedAddress = response.data.results[0].formatted_address;
                let googleMapsUrl = `https://maps.google.com/?q=${encodeURIComponent(validatedAddress)}`;

                node.debug(`Validated Address: ${validatedAddress}`);
                node.debug(`Google Maps URL: ${googleMapsUrl}`);

                // Step 5: Format the time (CST/CDT) using moment-timezone
                let now = moment().tz('America/Chicago');
                let formattedTime = now.format('HH:mm, MMMM Do, YYYY [CST/CDT]');
                let timezoneAbbreviation = now.isDST() ? 'CDT' : 'CST';
                formattedTime = formattedTime.replace('CST/CDT', timezoneAbbreviation);

                // Step 6: Create the final formatted message
                let formattedMessage = `Attention all personnel: A call for service has just been received. Immediate response is required!\n\n`;
                formattedMessage += `Incident Details:\n`;
                formattedMessage += `Type: ${callTypeMatch}\n`;
                formattedMessage += `Location: ${validatedAddress}\n`;
                formattedMessage += `Dispatch Time: ${formattedTime}\n\n`;
                formattedMessage += `Please acknowledge and respond immediately!\n\n`;
                formattedMessage += `Listen to the audio here: ${audioUrl}\n\n`;
                formattedMessage += `${message}`;

                // Retrieve Pushover token and user key from the global context
                const departmentConfig = global.get('pushoverDepartments')[department];
                if (!departmentConfig) {
                    node.error(`No Pushover configuration found for department: ${department}`);
                    return;
                }

                const pushoverPayload = {
                    token: departmentConfig.pushover_alert_app_token, // Get the correct department's token
                    user: departmentConfig.pushover_record_group_key, // Get the correct department's user/group key
                    message: formattedMessage,
                    title: title,
                    sound: "alien",  // Custom sound for the notification
                    priority: 2,  // Emergency priority
                    retry: 60,  // Retry interval
                    expire: 3600,  // Expire duration for emergency notifications
                    url: googleMapsUrl,  // Google Maps URL as the primary link
                    url_title: validatedAddress || "Play Audio"  // Use validated address or "Play Audio" for the URL button title
                };

                // Log the message being sent (for debugging purposes)
                node.debug(`Sending Pushover notification for ${department} with message: ${formattedMessage}`);

                // Send the Pushover payload to the HTTP node
                msg.payload = pushoverPayload;
                node.send([msg, null]);  // Send the message to the next node
            } else {
                node.warn("No valid address found via Google API. Using original address.");
                fallbackMessage();
            }
        })
        .catch(error => {
            node.error(`Error contacting Google API: ${error}. Falling back to original address.`);
            fallbackMessage();
        });
} else {
    node.warn("No address found to process. Sending the original payload.");
    fallbackMessage();
}

// Fallback message if Google API fails or no address is found
function fallbackMessage() {
    let now = moment().tz('America/Chicago');
    let formattedTime = now.format('HH:mm, MMMM Do, YYYY [CST/CDT]');
    let timezoneAbbreviation = now.isDST() ? 'CDT' : 'CST';
    formattedTime = formattedTime.replace('CST/CDT', timezoneAbbreviation);

    // Create fallback message with original address and no Google Maps link
    let fallbackMessage = `Attention all personnel: A call for service has just been received. Immediate response is required!\n\n`;
    fallbackMessage += `Incident Details:\n`;
    fallbackMessage += `Type: ${callTypeMatch}\n`;
    fallbackMessage += `Location: ${address}\n`;
    fallbackMessage += `Dispatch Time: ${formattedTime}\n\n`;
    fallbackMessage += `Please acknowledge and respond immediately!\n\n`;
    fallbackMessage += `Listen to the audio here: ${audioUrl}\n\n`;
    fallbackMessage += `${message}`;

    // Prepare the fallback payload
    const departmentConfig = global.get('pushoverDepartments')[department];
    const fallbackPayload = {
        token: departmentConfig.pushover_alert_app_token,
        user: departmentConfig.pushover_record_group_key,
        message: fallbackMessage,
        title: title,
        sound: "alien",
        priority: 2,
        retry: 60,
        expire: 3600,
        url: audioUrl,  // Use audio URL as fallback
        url_title: address || "Play Audio"  // Use address or "Play Audio" as fallback
    };
    
    msg.payload = fallbackPayload;
    node.send([msg, null]);  // Send the fallback message to the next node
}

// Log the script version
node.warn(`Running script version v1.5.3`);
