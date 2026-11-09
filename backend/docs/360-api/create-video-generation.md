# 创建视频生成

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
      summary: 创建视频生成
      deprecated: false
      description: 根据 prompt 和图片生成视频
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
                  description: 模型名称，通过模型广场搜索
                  examples:
                    - openai/gpt-image-1
                content:
                  type: array
                  items:
                    type: object
                    properties:
                      type:
                        type: string
                        description: 输入内容的类型
                        enum:
                          - text
                          - image_url
                        x-apifox-enum:
                          - value: text
                            name: ''
                            description: 输入给模型的内容为文本
                          - value: image_url
                            name: ''
                            description: 输入给模型的内容为图片
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
                        description: '如果"type" : “image_url”，则需要传入图片'
                    required:
                      - type
                    x-apifox-orders:
                      - type
                      - text
                      - image_url
                  description: 目前支持的输入组合： 文本+图、图、文本
                duration:
                  type: integer
                  description: 视频时长，单位秒，可选参数。根据模型名称，填写对应的值。比如 vidu 支持 4 秒和 8 秒，则写入 4
                extra_body:
                  type: object
                  properties: {}
                  x-apifox-orders: []
                  description: >-
                    模型自有参数，key-value 方式写入。举例：vidu
                    模型自有参数-分辨率(resolution)、可灵模型自有参数尾帧(image_tail)、视频模式(mode)等参数放入该结构中,会透传给模型
                user:
                  type: string
                  description: 终端用户唯一标识
              required:
                - model
                - duration
                - extra_body
              # 注（2026-10-08）：required 为上游声明值；实测按模型宽松——Hailuo 路径
              # 无 extra_body 可用、happyhorse 无顶层 duration/content 可用
              #（qihu_video.py / happyhorse.py 实证）。
              x-apifox-orders:
                - model
                - content
                - duration
                - extra_body
                - user
            examples:
              '1':
                value:
                  model: volcengine/doubao-seedance-2-0
                  content:
                    - type: text
                      text: >-
                        写实风格，晴朗的蓝天之下，一大片白色的雏菊花田，镜头逐渐拉近，最终定格在一朵雏菊花的特写上，花瓣上有几颗晶莹的露珠
                  extra_body:
                    ratio: '16:9'
                    duration: 5
                    watermark: false
                summary: seedance 2.0（文生视频）
                description: >-
                  额外参数可以参考官方文档：https://www.volcengine.com/docs/82379/1520757?lang=zh
              '2':
                value:
                  model: volcengine/doubao-seedance-2-0
                  content:
                    - type: text
                      text: >-
                        全程使用视频1的第一视角构图，全程使用音频1作为背景音乐。第一人称视角果茶宣传广告，seedance牌「苹苹安安」苹果果茶限定款；首帧为图片1，你的手摘下一颗带晨露的阿克苏红苹果，轻脆的苹果碰撞声；2-4
                        秒：快速切镜，你的手将苹果块投入雪克杯，加入冰块与茶底，用力摇晃，冰块碰撞声与摇晃声卡点轻快鼓点，背景音：「鲜切现摇」；4-6
                        秒：第一人称成品特写，分层果茶倒入透明杯，你的手轻挤奶盖在顶部铺展，在杯身贴上粉红包标，镜头拉近看奶盖与果茶的分层纹理；6-8
                        秒：第一人称手持举杯，你将图片2中的果茶举到镜头前（模拟递到观众面前的视角），杯身标签清晰可见，背景音「来一口鲜爽」，尾帧定格为图片2。背景声音统一为女生音色。
                    - type: image_url
                      image_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic1.jpg
                    - type: image_url
                      image_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_image/r2v_tea_pic2.jpg
                    - type: video_url
                      video_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_video/r2v_tea_video1.mp4
                    - type: audio_url
                      audio_url:
                        url: >-
                          https://ark-project.tos-cn-beijing.volces.com/doc_audio/r2v_tea_audio1.mp3
                  extra_body:
                    role: reference
                    generate_audio: true
                    ratio: '16:9'
                    duration: 11
                    watermark: false
                summary: seedance 2.0（参考生视频）
                description: >-
                  额外参数可以参考官方文档：https://www.volcengine.com/docs/82379/1520757?lang=zh
              '3':
                value:
                  model: alibaba/happyhorse-1.0-i2v
                  extra_body:
                    input:
                      prompt: >-
                        一幅都市奇幻艺术的场景。一个充满动感的涂鸦艺术角色。一个由喷漆所画成的少年，正从一面混凝土墙上活过来。他一边用极快的语速演唱一首英文rap，一边摆着一个经典的、充满活力的说唱歌手姿势。场景设定在夜晚一个充满都市感的铁路桥下。灯光来自一盏孤零零的街灯，营造出电影般的氛围，充满高能量和惊人的细节。视频的音频部分完全由rap构成，没有其他对话或杂音。
                      media:
                        - type: first_frame
                          url: >-
                            https://help-static-aliyun-doc.aliyuncs.com/file-manage-files/zh-CN/20250925/wpimhv/rap.png
                    parameters:
                      resolution: 720P
                      duration: 10
                      watermark: true
                summary: happyhorse（图生视频）
                description: >-
                  额外参数可以参考官方文档：https://help.aliyun.com/zh/model-studio/happyhorse-image-to-video-api-reference?spm=a2c4g.11186623.help-menu-2400256.d_2_3_1.28b916e0YFoYGL
              '4':
                value:
                  model: alibaba/happyhorse-1.0-t2v
                  extra_body:
                    input:
                      prompt: 一个小女孩走在路上
                    parameters:
                      resolution: 1080P
                      duration: 5
                      seed: 42
                summary: happyhorse（文生视频）
                description: >-
                  额外参数可以参考官方文档：https://help.aliyun.com/zh/model-studio/happyhorse-text-to-video-api-reference?spm=a2c4g.11186623.help-menu-2400256.d_2_3_0.16e516e0zlf8y9
              '5':
                value:
                  model: alibaba/happyhorse-1.0-r2v
                  extra_body:
                    input:
                      prompt: 图 1 中的主角在图 2 的场景中奔跑，随后拿起图 3 中的道具。画面保持 3D 卡通风格，动作流畅。
                      media:
                        - type: reference_image
                          url: https://public-bucket.example.com/image\_01.jpg
                        - type: reference_image
                          url: https://public-bucket.example.com/image\_02.png
                        - type: reference_image
                          url: https://public-bucket.example.com/image\_03.jpeg
                    parameters:
                      resolution: 1080P
                      ratio: '16:9'
                      duration: 5
                      seed: null
                      watermark: false
                summary: happyhorse（参考生视频）
                description: >-
                  额外参数可以参考官方文档：https://help.aliyun.com/zh/model-studio/happyhorse-reference-to-video-api-reference?spm=a2c4g.11186623.help-menu-2400256.d_2_3_2.325b4a97ysVSqC
              '6':
                value:
                  model: alibaba/happyhorse-1.0-video-edit
                  extra_body:
                    input:
                      media:
                        - type: video
                          url: https://example.com/image.mp4
                      prompt: 让图片中的场景动起来
                    parameters:
                      resolution: 1080P
                      audio_setting: origin
                      seed: 42
                summary: happyhorse（视频编辑）
                description: >-
                  额外参数可以参考官方文档：https://help.aliyun.com/zh/model-studio/happyhorse-video-edit-api-reference?spm=a2c4g.11186623.help-menu-2400256.d_2_3_3.416616e0nsX4oO
              '7':
                value:
                  model: google/veo-3.1-generate-preview
                  extra_body:
                    instances:
                      - prompt: 大象自然地四处走动
                        image: https://p0.qhimg.com/t11098f6bcd3e0f24845444c739.png
                    parameters:
                      aspectRatio: '16:9'
                      durationSeconds: 8
                      generateAudio: true
                      personGeneration: allow_adult
                      resolution: 720p
                summary: veo系列
                description: >-
                  额外的参数可以参考官方文档：https://ai.google.dev/gemini-api/docs/video?hl=zh-cn&example=dialogue
              '8':
                value:
                  model: bytedance/doubao-seedance-2-0-fast
                  content:
                    - type: text
                      text: 图中女孩对着镜头说“茄子”，360度环绕运镜
                    - type: image_url
                      image_url:
                        url: asset://asset-20260407180709-njxq6
                  extra_body:
                    duration: 5
                summary: seedance真人素材生成
                description: >-
                  真人素材生成流程：

                  1. 根据生成图片中的示例创建真人素材库，获取素材库id。例如：asset-20260803164232-j68xm

                  2.
                  根据生成图片中的示例在素材库中创建真人素材，获取素材id。例如：asset://asset-20260803164232-j68xm

                  3. 使用素材id生成视频。
              '9':
                value:
                  model: minimax/MiniMax-H3
                  content:
                    - type: text
                      text: >-
                        Pull focus to the people in the background and add more
                        steam to the ramen bowl.
                    - type: image_url
                      image_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/H3_AA_I2VA/gallery/sr_v17_variants_seed42_43_20260724/inputs/4a3a90bf9100_KDmcbkhzYo5sjjxr9FqcVmWVnzb.png
                  extra_body:
                    resolution: 2K
                    duration: 5
                    ratio: adaptive  # 注：代码实测 adaptive 会 400，实际下发钳制到 16:9（qihu_video.py:355-359）
                summary: minimax-h3（图生视频）
              '10':
                value:
                  model: minimax/MiniMax-H3
                  content:
                    - type: text
                      text: A little girl grow up.
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/fe9d04da-f60e-444d-a2e0-18ae743add33.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/97b7cd08-764e-4b8b-a7bf-87a0bd898575.jpeg
                  extra_body:
                    resolution: 2K
                    duration: 5
                    ratio: adaptive  # 注：代码实测 adaptive 会 400，实际下发钳制到 16:9（qihu_video.py:355-359）
                summary: minimax-h3（首尾帧生视频）
              '11':
                value:
                  model: minimax/MiniMax-H3
                  content:
                    - type: text
                      text: >-
                        角色说话：Follow the wind, live free.Leave worries behind,
                        enjoy the moment，音色参考音频1
                    - type: video_url
                      video_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/h3_promo_eval_ref2va/gallery/sr_v2p26_trio_seed42_20260724/inputs/297573323635_00_%E8%A7%86%E9%A2%911_YnyRbxEwio_video_20260525_163755_1927e9d3.mp4
                    - type: audio_url
                      audio_url:
                        url: >-
                          https://cdn.hailuoai.com/prod/hailuo_demo/testsets/h3_promo_eval_ref2va/gallery/sr_v2p26_trio_seed42_20260724/inputs/f463d523c5ce_01_%E9%9F%B3%E9%A2%911_RSLcbpzJPo_6%E6%9C%885%E6%97%A5(1).mp3
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/fe9d04da-f60e-444d-a2e0-18ae743add33.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/97b7cd08-764e-4b8b-a7bf-87a0bd898575.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/fe9d04da-f60e-444d-a2e0-18ae743add33.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/97b7cd08-764e-4b8b-a7bf-87a0bd898575.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/fe9d04da-f60e-444d-a2e0-18ae743add33.jpeg
                    - type: image_url
                      image_url:
                        url: >-
                          https://filecdn.minimax.chat/public/97b7cd08-764e-4b8b-a7bf-87a0bd898575.jpeg
                  extra_body:
                    role: reference
                    resolution: 2K
                    duration: 5
                    ratio: adaptive  # 注：代码实测 adaptive 会 400，实际下发钳制到 16:9（qihu_video.py:355-359）
                summary: minimax-h3（参考生视频）
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
                  id: 830674162707124224^^vidu-2.0^^0
                  status: created
                  failure_reason: ''
                  url: ''
                  duration: ''
          headers: {}
          x-apifox-name: 成功
      security: []
      x-apifox-folder: 视频生成
      x-apifox-status: developing
      x-run-in-apifox: https://app.apifox.com/web/project/7786134/apis/api-413292019-run
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
