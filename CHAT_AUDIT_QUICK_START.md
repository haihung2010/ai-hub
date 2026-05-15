# Chat Audit Feature — Quick Start Guide

## Where to Find Request/Response Data

### Step 1: Open Admin OS
Navigate to: `http://localhost:8000/admin.html`

### Step 2: Locate Chat Audit Tab
In the left sidebar, you'll see these tabs:
- Dashboard
- GPU Command
- Access Keys
- RAG Knowledge
- Tenants
- **Chat Audit** ← Click here
- System

### Step 3: Enter User Information
```
┌─────────────────────────────────────────┐
│ REQUEST/RESPONSE AUDIT                  │
├─────────────────────────────────────────┤
│                                         │
│ User ID (required)                      │
│ [____________________________]           │
│                                         │
│ Project ID (optional)                   │
│ [____________________________]           │
│                                         │
│ [Load Messages]                         │
│                                         │
└─────────────────────────────────────────┘
```

### Step 4: View Request/Response Pairs
Once loaded, you'll see:

```
┌─────────────────────────────────────────┐
│ Request #1                              │
│ 2026-05-15 14:30:00                     │
├─────────────────────────────────────────┤
│ What is the capital of France?          │
│                                         │
├─────────────────────────────────────────┤
│ Response                                │
│ 2026-05-15 14:30:05                     │
├─────────────────────────────────────────┤
│ The capital of France is Paris.         │
│                                         │
└─────────────────────────────────────────┘

┌─────────────────────────────────────────┐
│ Request #2                              │
│ 2026-05-15 14:31:00                     │
├─────────────────────────────────────────┤
│ Tell me more about Paris                │
│                                         │
├─────────────────────────────────────────┤
│ Response                                │
│ 2026-05-15 14:31:08                     │
├─────────────────────────────────────────┤
│ Paris is the largest city in France...  │
│                                         │
└─────────────────────────────────────────┘
```

## What You Can See

✅ **User's Questions**: Exact text of what users asked  
✅ **AI Responses**: Complete responses from the AI  
✅ **Timestamps**: When each message was created  
✅ **Project Context**: Which project the conversation was in  
✅ **Message Pairs**: Automatically grouped requests with responses  

## Example Usage Scenarios

### Scenario 1: Debug a User's Conversation
```
User ID: john_doe
Project ID: medical_chatbot
→ See all of John's questions and responses in the medical chatbot
```

### Scenario 2: Audit Compliance
```
User ID: compliance_user
Project ID: (leave empty)
→ See all conversations across all projects for compliance review
```

### Scenario 3: Analyze Conversation Quality
```
User ID: test_user
Project ID: customer_support
→ Review request/response pairs to evaluate AI quality
```

## Data You'll See

Each message contains:
- **Role**: "user" or "assistant"
- **Content**: The actual message text
- **Created At**: ISO timestamp of when message was created
- **Project ID**: Which project the conversation belongs to
- **Is Summarized**: Whether the message was included in a summary

## Filtering Options

| Field | Required | Purpose |
|-------|----------|---------|
| User ID | Yes | Fetch messages for specific user |
| Project ID | No | Filter by specific project |
| Limit | No | Max messages to load (default: 100) |

## Common Questions

**Q: Can I see all users' messages?**  
A: No, you must specify a User ID. This is for security and performance.

**Q: How many messages can I load at once?**  
A: Up to 100 messages per load. You can load more by making multiple requests.

**Q: Can I search within messages?**  
A: Currently no, but you can filter by User ID and Project ID. Full-text search is a planned enhancement.

**Q: Can I edit or delete messages?**  
A: No, the Chat Audit tab is read-only for data integrity.

**Q: Are messages real-time?**  
A: Messages are fetched from the database when you click "Load Messages". They're not live-streamed.

## Technical Details

**API Endpoint Used:**
```
GET /v1/admin/users/{user_id}/messages?project_id={project_id}&limit=100
```

**Authentication:**
- Requires valid `X-API-KEY` header (admin key)
- Only admin users can access this feature

**Data Source:**
- Messages are stored in the PostgreSQL `messages` table
- Linked to users and sessions for context

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No messages found" | Verify User ID exists and has messages |
| "User ID required" | Enter a valid User ID in the field |
| API Error | Check admin API key is set correctly |
| Empty responses | Some messages may not have responses yet |

## Next Steps

1. **Try it out**: Enter a User ID and load messages
2. **Review data**: Examine request/response pairs
3. **Analyze patterns**: Look for common questions or issues
4. **Export data**: (Future feature) Export conversations for analysis
5. **Monitor quality**: Use Chat Audit to track AI response quality

---

**Feature**: Chat Audit Tab  
**Status**: ✅ Available in Admin OS  
**Access**: http://localhost:8000/admin.html → Chat Audit tab  
**Last Updated**: 2026-05-15
