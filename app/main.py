import os
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Annotated, BinaryIO

import aiofiles
from fastapi import BackgroundTasks, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi_socketio import SocketManager
from passlib.hash import apr_md5_crypt
from python_on_whales import DockerClient
from rich.prompt import Prompt
from slugify import slugify

from prisma import Prisma

docker = DockerClient()

ROOT_PATH = Path(__file__).parent.parent.absolute()

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

    info = docker.system.info()

    if info.swarm.local_node_state == "inactive":
        docker.swarm.init()

    networks = docker.network.list(filters={"name": "traefik-public"})

    if not networks:
        docker.network.create(name="traefik-public", driver="overlay")
        docker.node.update(info.swarm.node_id, labels_add={"traefik-public.traefik-public-certificates": "1"})

        email = Prompt.ask("Enter your email address for Let's Encrypt")
        domain = Prompt.ask("Enter your domain name")
        username = Prompt.ask("Enter your username")
        password = Prompt.ask("Enter your password", password=True)

        os.environ["EMAIL"] = email
        os.environ["DOMAIN"] = domain
        os.environ["USERNAME"] = username
        os.environ["HASHED_PASSWORD"] = apr_md5_crypt.hash(password)

        docker.stack.deploy(
            compose_files=ROOT_PATH / "stacks" / "traefik-host.yml",
            name="traefik"
        )


@app.on_event("shutdown")
async def shutdown():
    if prisma.is_connected:
        await prisma.disconnect()


@app.get("/deploy", response_class=HTMLResponse)
def deploy_page(request: Request):
    return templates.TemplateResponse("deploy.html.j2", {"request": request})


async def deploy_service(slug: str, file: BinaryIO, app_id: int):
    async with aiofiles.tempfile.TemporaryDirectory() as tmp_dir:
        async with aiofiles.tempfile.NamedTemporaryFile(dir=tmp_dir, suffix=".zip") as tmp_file:
            await tmp_file.write(await file.read())
            await tmp_file.seek(0)
            await tmp_file.flush()

            with zipfile.ZipFile(tmp_file.name, "r") as zip_ref:
                zip_ref.extractall(tmp_dir)

        stream = docker.build(
            context_path=tmp_dir,
            stream_logs=True,
            tags=[slug]
        )

        for line in stream:
            logs_map[f"build_{slug}"].append(line)
            await socket.emit("build", [line], room=f"build_{slug}")

    service = docker.service.create(image=slug, command=None)

    await prisma.service.create(
        data={
            "id": service.id,
            "appId": app_id
        }
    )

    del logs_map[f"build_{slug}"]


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
