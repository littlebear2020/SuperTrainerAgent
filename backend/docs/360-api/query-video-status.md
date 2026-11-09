# 查询视频生成状态

## OpenAPI Specification

```yaml
openapi: 3.0.1
info:
  title: ''
  description: ''
  version: 1.0.0
paths:
  /v1/videos/async_generations/{id}:
    get:
      summary: 查询视频生成状态
      deprecated: false
      description: 根据创建视频生成任务阶段返回 id 字段，获取视频 url
      tags:
        - 视频生成
      parameters:
        - name: id
          in: path
          description: 生视频任务 id
          required: true
          example: 830674162707124224^^vidu-2.0^^0
          schema:
            type: string
        - name: Content-Type
          in: header
          description: 非必填（apifox 导出噪音；代码实测 _poll 只发 Authorization，qihu_video.py:396）
          required: false
          example: application/json
          schema:
            type: string
        - name: Authorization
          in: header
          description: ''
          required: true
          example: Bearer {{YOUR_API_KEY}}
          schema:
            type: string
            default: Bearer {{YOUR_API_KEY}}
      responses:
        '200':
          description: ''
          content:
            application/json:
              schema:
                type: object
                properties:
                  created:
                    type: integer
                    description: 创建的时间戳
                  data:
                    type: object
                    properties:
                      id:
                        type: string
                        description: 生视频任务 id，厂商 id^^模型名称^^生视频类型
                      status:
                        type: string
                        description: 厂商返回的任务状态
                      failure_reason:
                        type: string
                        description: 失败原因，错误时返回
                      url:
                        type: string
                        description: 视频 url
                      duration:
                        type: string
                        description: 视频时长
                    required:
                      - id
                      - status
                      - failure_reason
                      - url
                      - duration
                    x-apifox-orders:
                      - id
                      - status
                      - failure_reason
                      - url
                      - duration
                    description: 生成的视频信息
                required:
                  - created
                  - data
                x-apifox-orders:
                  - created
                  - data
              example:
                created: 0
                data:
                  id: 830674162707124224^^vidu-2.0^^0
                  status: success
                  failure_reason: ''
                  url: >-
                    https://prod-ss-vidu.s3.cn-northwest-1.amazonaws.com.cn/infer/tasks/25/0612/09/830674162707124224/creation-01/video.mp4?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA-REDACTED&X-Amz-Date=20250612T091716Z&X-Amz-Expires=86400&X-Amz-SignedHeaders=host&response-cache-control=max-age%3D86400&response-content-disposition=attachment%3Bfilename%3Dvidu-general-4-2025-06-12T09%253A14%253A51Z.mp4&x-id=GetObject&X-Amz-Signature=REDACTED
                  duration: ''
          headers: {}
          x-apifox-name: 成功
      security: []
      x-apifox-folder: 视频生成
      x-apifox-status: developing
      x-run-in-apifox: https://app.apifox.com/web/project/7786134/apis/api-413292020-run
components:
  schemas: {}
  securitySchemes:
    apiKeyAuth:
      type: apikey
      in: header
      name: Authorization
servers:
  - url: https://api.360.cn
    description: 正式环境
security: []

```
