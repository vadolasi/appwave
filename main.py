import aiodocker
from typing import Annotated, BinaryIO
from fastapi import BackgroundTasks, FastAPI, Request, File, Form, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prisma import Prisma
from slugify import slugify

docker = aiodocker.Docker()

prisma = Prisma()

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="templates")


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


async def deploy_service(name: str, file: BinaryIO, app_id: int):
    stream = docker.images.build(
        fileobj=file.file,
        tag=name,
        stream=True,
        encoding="utf-8"
    )

    async for line in stream:
        print(line)

    service = await docker.services.create(
        task_template={
            "ContainerSpec": {
                "Image": name
            }
        },
        name=name
    )

    await prisma.service.create(
        data={
            "id": service["ID"],
            "appId": app_id
        }
    )


@app.post("/deploy", response_class=RedirectResponse)
async def deploy(
    name: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
    background_tasks: BackgroundTasks
):
    app = await prisma.app.create(
        data={
            "name": name,
            "slug": slugify(name)
        }
    )

    background_tasks.add_task(deploy_service, name, file, app.id)

    return "/"


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
