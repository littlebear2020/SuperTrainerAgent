# 创建视频生成（seedance）

## OpenAPI Specification

```yaml
openapi: 3.0.1
info:
  title: ''
  description: ''
  version: 1.0.0
paths:
  /v1/videos/async_generations:
    post:
      summary: 创建视频生成（seedance）
      deprecated: false
      description: |-
        该接口用于创建seedance模型的异步视频生成任务，支持以下 6 种生成类型：

        1. 文生视频
        2. 图生视频（有声视频-首帧）
        3. 有声视频-首尾帧
        4. 延长视频
        5. 编辑视频
        6. 多模态参考
      tags:
        - 视频生成
      parameters:
        - name: Content-Type
          in: header
          description: ''
          required: true
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
      requestBody:
        content:
          application/json:
            schema:
              type: object
              properties:
                model:
                  type: string
                  description: >-
                    模型名称，例如 bytedance/doubao-seedance-2-5。（注意：不是所有模型都支持以上 6
                    种生成类型，具体支持的生成类型可以参考[官方文档](https://docs.volcengine.com/docs/82379/1520757?lang=zh)）
                  examples:
                    - bytedance/doubao-seedance-2-5
                content:
                  type: array
                  items:
                    type: object
                    properties:
                      type:
                        type: string
                        description: 输入内容的类型，支持文本、图片、视频和音频类型
                        enum:
                          - text
                          - image_url
                          - video_url
                          - audio_url
                        x-apifox-enum:
                          - value: text
                            name: ''
                            description: 输入给模型的内容为文本
                          - value: image_url
                            name: ''
                            description: 输入给模型的内容为图片
                          - value: video_url
                            name: ''
                            description: 输入给模型的内容为视频
                          - value: audio_url
                            name: ''
                            description: 输入给模型的内容为音频
                      text:
                        type: string
                        description: '如果"type" : “text”，则需要传入文本。文本的信息，可以是描述期望生成的视频'
                      image_url:
                        type: object
                        properties:
                          url:
                            type: string
                            description: 图片的信息，可以是图片 URL（确保可访问）或图片 Base64 编码。
                        x-apifox-orders:
                          - url
                        description: '如果"type" : “image_url”，则需要传入图片信息'
                      video_url:
                        type: object
                        properties:
                          url:
                            type: string
                            description: 视频地址
                        x-apifox-orders:
                          - url
                        description: '如果"type" : “video_url”，则需要传入视频信息'
                      audio_url:
                        type: object
                        properties:
                          url:
                            type: string
                            description: 音频地址
                        x-apifox-orders:
                          - url
                        description: '如果"type" : “audio_url”，则需要传入音频信息'
                    required:
                      - type
                    x-apifox-orders:
                      - type
                      - text
                      - image_url
                      - video_url
                      - audio_url
                  description: 输入内容列表，支持文本、图片、视频和音频。
                extra_body:
                  type: object
                  properties: {}
                  x-apifox-orders: []
                  description: >-
                    不同生成类型下，`extra_body` 中会包含不同参数，具体参数可以参考[ seedance
                    官方文档](https://docs.volcengine.com/docs/82379/1520757?lang=zh)）。常见字段如下：


                    | 参数名         | 类型    | 是否必填 |
                    说明                                              |

                    | -------------- | ------- | -------- |
                    ------------------------------------------------- |

                    | role           | string  | 否       |
                    生成类型标识。参考生视频：`reference`             |

                    | duration       | integer | 否       |
                    视频时长，示例值：`5`                             |

                    | ratio          | string  | 否       |
                    视频宽高比，示例值：`"16:9"`                      |

                    | resolution     | string  | 否       |
                    视频分辨率，可选值：`480p`、`720p`、`1080p`、`4k` |

                    | generate_audio | boolean | 否       |
                    是否生成有声视频。                                |
              required:
                - model
                - extra_body
              x-apifox-orders:
                - model
                - content
                - extra_body
            examples:
              '1':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: >-
                        写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上，花瓣上有几颗晶莹的露珠
                  extra_body:
                    ratio: '16:9'
                    duration: 5
                    watermark: false
                summary: 文生视频示例
              '2':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: 女孩抱着狐狸，女孩睁开眼，温柔地看向镜头，狐狸友善地抱着，镜头缓缓拉出，女孩的头发被风吹动，可以听到风声
                    - type: image_url
                      image_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_image/i2v_foxrgirl.png
                  extra_body:
                    generate_audio: true
                    ratio: adaptive
                    duration: 5
                    watermark: false
                summary: 图生视频（有声视频-首帧）示例
              '3':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: 图中女孩对着镜头说“茄子”，360度环绕运镜
                    - type: image_url
                      image_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_first_frame.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_image/seepro_last_frame.jpeg
                  extra_body:
                    generate_audio: true
                    ratio: adaptive
                    duration: 5
                    watermark: false
                summary: 有声视频-首尾帧示例
                description: 说明：第一张传入的图像会被作为首帧，第二张传入图像作为尾帧。
              '4':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: 延长@视频1，窗户打开后进入@视频2的美术馆室内，最后镜头进入@视频3的画内
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/r2v_extend_video1_75.mov
                    - type: video_url
                      video_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video2.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_extend_video3.mp4
                  extra_body:
                    role: reference
                    generate_audio: true
                    ratio: adaptive
                    duration: 11
                    omni_reference_task_type: extend
                    output_format: mov
                summary: 延长视频示例
                description: >-
                  说明：在`extra_body`中设置生成类型标识`role`的值为参考生视频：`reference`，会将所有的图像、视频和音频对象设置为参考类型。
              '5':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: 视频编辑：删除 @视频1中的所有人，除了主角。
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_edit_input.mov
                  extra_body:
                    role: reference
                    generate_audio: true
                    ratio: adaptive
                    duration: -1
                    omni_reference_task_type: edit
                    output_format: mov
                summary: 编辑视频示例
              '6':
                value:
                  model: bytedance/doubao-seedance-2-5
                  content:
                    - type: text
                      text: >-
                        明亮多彩的广告片风格，果味饼干为主角，包含草莓、苹果、葡萄、橙子四种口味，草莓味参考@图像1，饼干与对应水果以强秩序感的几何阵列排布，整体画面干净、高级、节奏强。开场水果快速建立视觉聚焦，参考@视频1的构图，音乐重拍切入。随后不同口味饼干整齐排列，切特写，参考@视频2的动态和运镜。高潮段一块饼干被折断，瞬间进入慢动作，果味夹心爆开，碎屑飞溅，果汁感与颗粒冲击被放大展示，参考@视频3的冲击感。横向阵列，形成节奏抛物感，参考@视频4的运动，突出秩序美感与产品丰富度。随后迅速回到快节奏剪辑。结尾英文文字
                        One bite of crispness, a heart full of delight
                        快速分词切换入画，配合强节奏文字运动与产品定格，参考@视频5，最终品牌感收束，饼干和水果向四周发散，参考@视频6画面充满年轻、活力、好吃、想分享的广告氛围。
                    - type: image_url
                      image_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/images/video-generation/seedance2.5_reference1.png
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference2.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference3.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference4.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference5.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference6.mp4
                    - type: video_url
                      video_url:
                        url: >-
                          https://arkdocs.tos-cn-beijing.volces.com/videos/video-generation/seedance2.5_reference7.mp4
                  extra_body:
                    role: reference
                    generate_audio: true
                    ratio: '16:9'
                    duration: 15
                summary: 多模态参考示例
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
                created: 1749719668
                data:
                  id: 830674162707124224^^seedance-2-0^^0
                  status: created
                  failure_reason: ''
                  url: ''
                  duration: ''
          headers: {}
          x-apifox-name: 成功
      security: []
      x-apifox-folder: 视频生成
      x-apifox-status: released
      x-run-in-apifox: https://app.apifox.com/web/project/7786134/apis/api-503146854-run
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
