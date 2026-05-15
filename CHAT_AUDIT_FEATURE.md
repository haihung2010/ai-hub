# Chat Audit Feature — Admin UI

**Date**: 2026-05-15  
**Status**: ✅ IMPLEMENTED

## Overview

The Chat Audit tab in the Admin OS provides a way to view actual request/response pairs from chat conversations stored in the database. This allows administrators to:

- Inspect real user queries and AI responses
- Debug conversation flows
- Audit chat history for compliance
- Analyze conversation patterns
- Verify data integrity

## Accessing Chat Audit

1. Open the Admin OS at `http://localhost:8000/admin.html` (or your deployment URL)
2. Click the **Chat Audit** tab in the left sidebar
3. Enter a **User ID** (required) and optionally a **Project ID**
4. Click **Load Messages** to fetch the conversation history

## Features

### Request/Response Display

- **Request Section**: Shows the user's message with timestamp
- **Response Section**: Shows the AI's response with timestamp
- **Formatting**: Messages are displayed in monospace font with proper escaping
- **Scrollable**: Long messages can be scrolled within their containers
- **Pair Grouping**: Messages are automatically grouped into request/response pairs

### Filtering Options

- **User ID**: Filter messages by specific user (required)
- **Project ID**: Filter messages by specific project (optional)
- **Limit**: Automatically loads up to 100 most recent messages

### Data Display

Each message pair shows:
- Request number (e.g., "Request #1")
- User message content
- Timestamp of request
- AI response content
- Timestamp of response
- Status indicator if response is missing

## Technical Details

### Backend Endpoint

The feature uses the existing admin endpoint:

```
GET /v1/admin/users/{user_id}/messages?project_id={project_id}&limit=100
```

**Parameters:**
- `user_id` (required): The user ID to fetch messages for
- `project_id` (optional): Filter by project
- `limit` (optional): Maximum number of messages to return (default: 100)

**Response Format:**
```json
[
  {
    "id": "msg_123",
    "role": "user",
    "content": "What is 2+2?",
    "created_at": "2026-05-15T14:30:00Z",
    "project_id": "test_project",
    "is_summarized": 0
  },
  {
    "id": "msg_124",
    "role": "assistant",
    "content": "2+2 equals 4.",
    "created_at": "2026-05-15T14:30:05Z",
    "project_id": "test_project",
    "is_summarized": 0
  }
]
```

### Frontend Implementation

**HTML Elements:**
- `#audit-user-id`: Input field for User ID
- `#audit-project-id`: Input field for Project ID
- `#audit-load-btn`: Button to trigger message loading
- `#audit-messages-container`: Container for displaying messages

**JavaScript Functions:**
- `initAuditTab()`: Initializes the tab and binds event listeners
- `loadAuditMessages()`: Fetches messages from the API and renders them

### Message Pairing Logic

Messages are grouped into pairs by iterating through the response array:
- Even indices (0, 2, 4...) are treated as user messages
- Odd indices (1, 3, 5...) are treated as assistant responses
- If a user message has no corresponding response, it's displayed with "No response yet"

## Usage Examples

### Example 1: View all messages for a user

1. Enter User ID: `user_123`
2. Leave Project ID empty
3. Click "Load Messages"
4. View all conversations across all projects for this user

### Example 2: View messages for a specific project

1. Enter User ID: `user_456`
2. Enter Project ID: `medical_chatbot`
3. Click "Load Messages"
4. View only conversations in the medical_chatbot project

### Example 3: Audit compliance

1. Enter User ID: `compliance_audit_user`
2. Leave Project ID empty
3. Click "Load Messages"
4. Review all request/response pairs for compliance verification
5. Check timestamps and content for audit trail

## Security Considerations

- **Authentication**: Requires valid `X-API-KEY` header (admin key)
- **Authorization**: Only admin users can access the Chat Audit tab
- **Data Escaping**: All user content is HTML-escaped to prevent XSS
- **Read-Only**: The feature only displays data, no modifications possible
- **Rate Limiting**: Subject to standard API rate limiting

## Limitations

- Maximum 100 messages per load (configurable via API)
- Messages must be loaded by User ID (cannot browse all users)
- No full-text search across message content
- No filtering by date range
- No export functionality (can be added in future)

## Future Enhancements

Potential improvements for future versions:

1. **Date Range Filtering**: Filter messages by date range
2. **Full-Text Search**: Search message content across all users
3. **Export**: Export messages as CSV or JSON
4. **Analytics**: Show conversation statistics (avg response time, etc.)
5. **Sentiment Analysis**: Display sentiment scores for messages
6. **Bulk Operations**: Delete or archive multiple conversations
7. **Message Editing**: Admin ability to redact sensitive information
8. **Conversation Threading**: Group related messages into threads

## Troubleshooting

### "No messages found"

- Verify the User ID is correct
- Check that messages exist in the database for this user
- Ensure the user has had conversations in the specified project

### "User ID required"

- The User ID field is mandatory
- Enter a valid user ID before clicking Load Messages

### API Error

- Verify your admin API key is set correctly
- Check that the backend service is running
- Review browser console for detailed error messages

## Related Features

- **User Detail View**: See user profile, pinned memories, and summaries
- **Session Management**: View active sessions and their activity
- **Knowledge Base**: Manage RAG knowledge cards
- **System Health**: Monitor provider health and security events

---

**Implementation Date**: 2026-05-15  
**Feature Status**: ✅ Production Ready  
**Last Updated**: 2026-05-15
