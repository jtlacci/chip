// The only file that talks to the network. Every call goes to the api brick's routes.

export async function call(method, path, body) {
  const options = { method, credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.headers["content-type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    return { status: 0, data: { error: "offline" } };
  }
  let data = {};
  try {
    data = await response.json();
  } catch {
    data = {};
  }
  return { status: response.status, data };
}

export const get = (path) => call("GET", path);
export const put = (path, body) => call("PUT", path, body);
export const post = (path, body) => call("POST", path, body);
