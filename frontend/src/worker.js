export default {
  async fetch(request, env) {
    try {
      return await env.ASSETS.fetch(request);
    } catch {
      return new Response("Not found", { status: 404 });
    }
  },
};
