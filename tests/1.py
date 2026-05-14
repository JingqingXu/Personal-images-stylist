import requests

url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

payload = {
    "model": "glm-4.6",
    "messages": [
        {
            "role": "user",
            "content": "写一首关于春天的诗。"
        }
    ],
    "stream": True,
    "temperature": 1
}
headers = {
    "Authorization": "fcd02357954f4606af4786d9d65946fa.82w8CgVuzCfrpaHh",
    "Content-Type": "application/json"
}



response = requests.post(url, json=payload, headers=headers)

print(response.text)


'''
{
  "id": "<string>",
  "request_id": "<string>",
  "created": 123,
  "model": "<string>",
  "choices": [
    {
      "index": 123,
      "message": {
        "role": "assistant",
        "content": "<string>",
        "reasoning_content": "<string>",
        "audio": {
          "id": "<string>",
          "data": "<string>",
          "expires_at": "<string>"
        },
        "tool_calls": [
          {
            "function": {
              "name": "<string>",
              "arguments": "<string>"
            },
            "mcp": {
              "id": "<string>",
              "type": "mcp_list_tools",
              "server_label": "<string>",
              "error": "<string>",
              "tools": [
                {
                  "name": "<string>",
                  "description": "<string>",
                  "annotations": {},
                  "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": [
                      "<string>"
                    ],
                    "additionalProperties": true
                  }
                }
              ],
              "arguments": "<string>",
              "name": "<string>",
              "output": {}
            },
            "id": "<string>",
            "type": "<string>"
          }
        ]
      },
      "finish_reason": "<string>"
    }
  ],
  "usage": {
    "prompt_tokens": 123,
    "completion_tokens": 123,
    "prompt_tokens_details": {
      "cached_tokens": 123
    },
    "total_tokens": 123
  },
  "video_result": [
    {
      "url": "<string>",
      "cover_image_url": "<string>"
    }
  ],
  "web_search": [
    {
      "icon": "<string>",
      "title": "<string>",
      "link": "<string>",
      "media": "<string>",
      "publish_date": "<string>",
      "content": "<string>",
      "refer": "<string>"
    }
  ],
  "content_filter": [
    {
      "role": "<string>",
      "level": 123
    }
  ]
}
'''