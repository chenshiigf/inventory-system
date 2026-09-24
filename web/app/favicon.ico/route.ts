export function GET(request: Request) {
  return Response.redirect(new URL("/icon.png", request.url));
}
