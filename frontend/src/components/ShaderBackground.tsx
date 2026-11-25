import { useEffect, useRef } from "react";

/**
 * Ported 1:1 from the Stitch export `shader/code.html` (STITCH_SHADER_START:ANIMATION_2).
 * A subtle animated WebGL "medical pulse" gradient used behind hero sections.
 * Same GLSL, same colors (#E53935 / #1565C0 / #F8FAFC), same mouse-reactive uniform.
 */
export default function ShaderBackground({ className = "fixed inset-0 w-full h-full" }: { className?: string }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    function syncSize() {
      const w = canvas!.clientWidth || 1280;
      const h = canvas!.clientHeight || 720;
      if (canvas!.width !== w || canvas!.height !== h) {
        canvas!.width = w;
        canvas!.height = h;
      }
    }

    let ro: ResizeObserver | undefined;
    if (typeof ResizeObserver !== "undefined") {
      ro = new ResizeObserver(syncSize);
      ro.observe(canvas);
    }
    syncSize();

    const gl = (canvas.getContext("webgl") ||
      canvas.getContext("experimental-webgl")) as WebGLRenderingContext | null;
    if (!gl) return;

    const vs = `attribute vec2 a_position;
varying vec2 v_texCoord;
void main() {
  v_texCoord = a_position * 0.5 + 0.5;
  gl_Position = vec4(a_position, 0.0, 1.0);
}`;
    const fs = `precision highp float;
uniform float u_time;
uniform vec2 u_resolution;
uniform vec2 u_mouse;

varying vec2 v_texCoord;

void main() {
    vec2 uv = v_texCoord;
    vec2 p = (uv * 2.0 - 1.0) * vec2(u_resolution.x / u_resolution.y, 1.0);

    // Abstract medical pulse/flow effect
    float t = u_time * 0.3;
    float pulse = sin(p.x * 2.0 + t) * sin(p.y * 3.0 - t * 0.5);

    vec3 color1 = vec3(0.898, 0.224, 0.208); // Medical Red #E53935
    vec3 color2 = vec3(0.082, 0.396, 0.753); // Royal Blue #1565C0
    vec3 bgColor = vec3(0.973, 0.980, 0.988); // Background #F8FAFC

    float mask = smoothstep(0.1, 0.8, pulse * 0.5 + 0.5);
    vec3 finalColor = mix(bgColor, mix(color2, color1, uv.x), mask * 0.05);

    // Add subtle medical grid
    vec2 grid = abs(fract(uv * 20.0 - 0.5) - 0.5) / fwidth(uv * 20.0);
    float line = min(grid.x, grid.y);
    finalColor = mix(finalColor, vec3(0.9, 0.9, 0.9), (1.0 - smoothstep(0.0, 0.02, line)) * 0.3);

    gl_FragColor = vec4(finalColor, 1.0);
}`;

    function compileShader(type: number, src: string) {
      const s = gl!.createShader(type)!;
      gl!.shaderSource(s, src);
      gl!.compileShader(s);
      return s;
    }

    const prog = gl.createProgram()!;
    gl.attachShader(prog, compileShader(gl.VERTEX_SHADER, vs));
    gl.attachShader(prog, compileShader(gl.FRAGMENT_SHADER, fs));
    gl.linkProgram(prog);
    gl.useProgram(prog);

    const buf = gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER, buf);
    gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1, -1, 1, -1, -1, 1, 1, 1]), gl.STATIC_DRAW);
    const posLoc = gl.getAttribLocation(prog, "a_position");
    gl.enableVertexAttribArray(posLoc);
    gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

    const uTime = gl.getUniformLocation(prog, "u_time");
    const uRes = gl.getUniformLocation(prog, "u_resolution");
    const uMouse = gl.getUniformLocation(prog, "u_mouse");

    // u_mouse is in pixel coordinates matching u_resolution (ShaderToy convention).
    const mouse = { x: canvas.width / 2, y: canvas.height / 2 };
    const onMouseMove = (event: MouseEvent) => {
      const rect = canvas.getBoundingClientRect();
      if (rect.width && rect.height) {
        const nx = (event.clientX - rect.left) / rect.width;
        const ny = 1.0 - (event.clientY - rect.top) / rect.height;
        mouse.x = nx * canvas.width;
        mouse.y = ny * canvas.height;
      }
    };
    window.addEventListener("mousemove", onMouseMove);

    let rafId = 0;
    function render(t: number) {
      if (typeof ResizeObserver === "undefined") syncSize();
      gl!.viewport(0, 0, canvas!.width, canvas!.height);
      if (uTime) gl!.uniform1f(uTime, t * 0.001);
      if (uRes) gl!.uniform2f(uRes, canvas!.width, canvas!.height);
      if (uMouse) gl!.uniform2f(uMouse, mouse.x, mouse.y);
      gl!.drawArrays(gl!.TRIANGLE_STRIP, 0, 4);
      rafId = requestAnimationFrame(render);
    }
    rafId = requestAnimationFrame(render);

    return () => {
      cancelAnimationFrame(rafId);
      window.removeEventListener("mousemove", onMouseMove);
      ro?.disconnect();
    };
  }, []);

  return (
    <div className={className}>
      <canvas ref={canvasRef} style={{ display: "block", width: "100%", height: "100%" }} />
    </div>
  );
}
