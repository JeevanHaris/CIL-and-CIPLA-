class WebThreads {
  constructor(canvasId, options = {}) {
    this.canvas = document.getElementById(canvasId);
    if (!this.canvas) return;
    this.ctx = this.canvas.getContext('2d');

    // Config based on the provided React props
    this.options = {
      color1: options.color1 || "#705656ff",
      color2: options.color2 || "#0e262c",
      color3: options.color3 || "#0c5e05ff",
      speed: options.speed || 0.2,
      threadCount: options.threadCount || 6,
      frequency: options.frequency || 5,
      spread: options.spread || 0.18,
      taper: options.taper || 1,
      position: options.position || 0.5,
      glow: options.glow || 0.02,
      thickness: options.thickness || 1.1,
      mouseInteraction: options.mouseInteraction !== false,
      mouseStrength: options.mouseStrength || 0.3,
      opacity: options.opacity || 1,
      brightness: options.brightness || 0.6
    };

    this.time = 0;
    this.mouse = { x: window.innerWidth / 2, y: window.innerHeight / 2 };
    this.targetMouse = { x: window.innerWidth / 2, y: window.innerHeight / 2 };

    this.resize = this.resize.bind(this);
    this.animate = this.animate.bind(this);
    this.handleMouseMove = this.handleMouseMove.bind(this);

    window.addEventListener('resize', this.resize);
    if (this.options.mouseInteraction) {
      window.addEventListener('mousemove', this.handleMouseMove);
    }

    this.resize();
    this.animate();
  }

  resize() {
    this.canvas.width = window.innerWidth;
    this.canvas.height = window.innerHeight;
  }

  handleMouseMove(e) {
    this.targetMouse.x = e.clientX;
    this.targetMouse.y = e.clientY;
  }

  animate() {
    this.time += 0.005 * this.options.speed;

    // Smooth mouse follow
    this.mouse.x += (this.targetMouse.x - this.mouse.x) * 0.05;
    this.mouse.y += (this.targetMouse.y - this.mouse.y) * 0.05;

    this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
    this.ctx.globalCompositeOperation = 'screen';

    const centerY = this.canvas.height * this.options.position;

    for (let i = 0; i < this.options.threadCount; i++) {
      const progress = i / (this.options.threadCount - 1 || 1);

      this.ctx.beginPath();
      const threadOffset = progress * Math.PI * 2;

      for (let x = 0; x <= this.canvas.width; x += 4) {
        const xProgress = x / this.canvas.width;
        let y = centerY;

        const freq = this.options.frequency;
        const amplitude = this.canvas.height * this.options.spread;

        // Complex wave forms combining multiple sines
        const wave1 = Math.sin(xProgress * freq * Math.PI * 2 + this.time * 5 + threadOffset);
        const wave2 = Math.sin(xProgress * freq * 1.5 * Math.PI * 2 - this.time * 3 + threadOffset * 1.5);
        const wave3 = Math.cos(xProgress * freq * 0.5 * Math.PI * 2 + this.time * 2 + threadOffset * 2);

        let wave = (wave1 * 0.5 + wave2 * 0.3 + wave3 * 0.2) * amplitude;

        // Taper
        if (this.options.taper > 0) {
          const taperVal = Math.sin(xProgress * Math.PI);
          wave *= taperVal * this.options.taper + (1 - this.options.taper);
        }

        // Mouse interaction
        if (this.options.mouseInteraction) {
          const dx = x - this.mouse.x;
          const dy = centerY - this.mouse.y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          const maxDist = 400;
          if (dist < maxDist) {
            const influence = Math.pow(1 - dist / maxDist, 2) * this.options.mouseStrength * 200;
            wave += Math.sin(xProgress * Math.PI * 10 - this.time * 10) * influence * (progress > 0.5 ? 1 : -1);
          }
        }

        y += wave;

        if (x === 0) {
          this.ctx.moveTo(x, y);
        } else {
          this.ctx.lineTo(x, y);
        }
      }

      const gradient = this.ctx.createLinearGradient(0, 0, this.canvas.width, 0);
      gradient.addColorStop(0, this.options.color2);
      gradient.addColorStop(0.5, this.options.color1);
      gradient.addColorStop(1, this.options.color3);

      this.ctx.strokeStyle = gradient;
      this.ctx.lineWidth = this.options.thickness;
      this.ctx.globalAlpha = this.options.opacity * this.options.brightness;

      if (this.options.glow > 0) {
        this.ctx.shadowBlur = this.options.glow * 1000;
        this.ctx.shadowColor = this.options.color1;
      }

      this.ctx.stroke();
    }

    // Reset alpha
    this.ctx.globalAlpha = 1;

    requestAnimationFrame(this.animate);
  }
}
