/**
 * 聊天页 mount 自动发送判定（抽出纯函数以便 node 环境单测）。
 *
 * 历史守卫：只在 /chats/new（isNewThread）自动发送，防止在既有线程里误发。
 * 2026-07-31 灵感库文件上传流：defer instantiate 已建好线程并上传完文件，
 * 落地既有线程也要自动发送——由 sessionStorage pending prompt 的
 * `existingThread: true` 显式放行（缺省 false，旧行为不变）。
 */
export function shouldAutoSend(opts: {
  autosend: boolean;
  text: string;
  isNewThread: boolean;
  allowExistingThread: boolean;
}): boolean {
  if (!opts.autosend || !opts.text) return false;
  if (!opts.isNewThread && !opts.allowExistingThread) return false;
  return true;
}
