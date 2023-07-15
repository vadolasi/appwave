from collections import defaultdict
from typing import Annotated, BinaryIO

import aiodocker
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_socketio import SocketManager
from slugify import slugify

from prisma import Prisma

docker = aiodocker.Docker()

prisma = Prisma()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

socket = SocketManager(app=app)

templates = Jinja2Templates(directory="templates")

logs_map: dict[str, list[str]] = defaultdict(list)


@socket.on("join")
async def join(sid, data):
    socket.enter_room(sid, data)
    await socket.emit("build", logs_map[data], to=sid)


@app.on_event("startup")
async def startup():
    await prisma.connect()

    info = await docker.system.info()

    if info["Swarm"]["LocalNodeState"] == "inactive":
        await docker.swarm.init()


@app.on_event("shutdown")
async def shutdown():
    if prisma.is_connected:
        await prisma.disconnect()


@app.get("/deploy", response_class=HTMLResponse)
def deploy_page(request: Request):
    return templates.TemplateResponse("deploy.html.j2", {"request": request})


async def deploy_service(slug: str, file: BinaryIO, app_id: int):
    stream = docker.images.build(
        fileobj=file.file,
        tag=slug,
        stream=True,
        encoding="utf-8",
        rm=True
    )

    async for line in stream:
        if line.get("stream"):
            logs_map[slug].append(line["stream"])
            await socket.emit("build", [line["stream"]], room=f"build_{slug}")
        elif line.get("errorDetail"):
            await socket.emit("error", line["errorDetail"]["message"], room=f"build_{slug}")

    service = await docker.services.create(
        task_template={
            "ContainerSpec": {
                "Image": slug
            }
        },
        name=slug
    )

    await prisma.service.create(
        data={
            "id": service["ID"],
            "appId": app_id
        }
    )


@app.post("/deploy", response_class=RedirectResponse, status_code=302)
async def deploy(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks
):
    slug = slugify(name)
    app = await prisma.app.create(
        data={
            "name": name,
            "slug": slug
        }
    )

    background_tasks.add_task(deploy_service, slug, file, app.id)

    return f"/app/{app.slug}/build_logs"


@app.get("/app/{slug}/build_logs", response_class=HTMLResponse)
async def build_logs(request: Request, slug: str):
    return templates.TemplateResponse(
        "build_logs.html.j2",
        {
            "request": request,
            "slug": slug
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
