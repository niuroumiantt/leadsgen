export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method: body === undefined ? "GET" : "POST",
    headers: {
      "Content-Type": "application/json",
      "X-Leadsgen-Client": "web-v1",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) {
    const error = await response
      .json()
      .catch(() => ({ detail: "服务响应异常" }));
    throw new Error(
      typeof error.detail === "string"
        ? error.detail
        : `请求失败 (${response.status})`,
    );
  }
  return response.json();
}
