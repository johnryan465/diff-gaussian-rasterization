#
# Copyright (C) 2023, Inria
# GRAPHDECO research group, https://team.inria.fr/graphdeco
# All rights reserved.
#
# This software is free for non-commercial, research and evaluation use
# under the terms of the LICENSE.md file.
#
# For inquiries contact  george.drettakis@inria.fr
#

from typing import NamedTuple, Optional, Tuple, Union
import torch.nn as nn
from torch import Tensor
import torch
from . import _C  # type: ignore
from jaxtyping import Float, jaxtyped
from typeguard import typechecked


class GaussianRasterizationSettings(NamedTuple):
    image_height: int
    image_width: int
    tanfovx: float
    tanfovy: float
    bg: torch.Tensor
    scale_modifier: float
    cov_offset: float
    viewmatrix: torch.Tensor
    projmatrix: torch.Tensor
    sh_degree: int
    prefiltered: bool
    debug: bool


def cpu_deep_copy_tuple(input_tuple):
    copied_tensors = [
        item.cpu().clone() if isinstance(item, torch.Tensor) else item
        for item in input_tuple
    ]
    return tuple(copied_tensors)


@jaxtyped
@typechecked
def rasterize_gaussians(
    means3D: Float[Tensor, "num_gaussians 3"],
    means2D: Float[Tensor, "num_gaussians 3"],
    sh: Float[Tensor, "num_gaussians num_coeffs num_channels"],
    colors_precomp: Union[Float[Tensor, "num_gaussians 3"], Float[Tensor, "*"]],
    opacities: Float[Tensor, "num_gaussians 1"],
    scales: Float[Tensor, "num_gaussians 3"],
    rotations: Float[Tensor, "num_gaussians 4"],
    cov3Ds_precomp: Tensor,
    camerapos: Float[Tensor, "3"],
    camerarot: Float[Tensor, "4 4"],
    raster_settings: GaussianRasterizationSettings,
):
    # print("rasterize_gaussians", means3D.shape)
    return _RasterizeGaussians.apply(
        means3D,
        means2D,
        sh,
        colors_precomp,
        opacities,
        scales,
        rotations,
        cov3Ds_precomp,
        camerapos,
        camerarot,
        raster_settings,
    )


class _RasterizeGaussians(torch.autograd.Function):
    @jaxtyped
    @typechecked
    @staticmethod
    def forward(
        ctx,
        means3D: Float[Tensor, "num_gaussians 3"],
        means2D: Float[Tensor, "num_gaussians 3"],
        sh: Float[Tensor, "num_gaussians num_coeffs num_channels"],
        colors_precomp: Union[Float[Tensor, "num_gaussians 3"], Float[Tensor, "*"]],
        opacities: Float[Tensor, "num_gaussians 1"],
        scales: Float[Tensor, "num_gaussians 3"],
        rotations: Float[Tensor, "num_gaussians 4"],
        cov3Ds_precomp: Tensor,
        camerapos: Tensor,
        camerarot: Tensor,
        raster_settings: GaussianRasterizationSettings,
    ) -> Tuple[Tensor, Tensor, Tensor]:
        # Restructure arguments the way that the C++ lib expects them
        args = (
            raster_settings.bg,
            means3D,
            colors_precomp,
            opacities,
            scales,
            rotations,
            raster_settings.scale_modifier,
            raster_settings.cov_offset,
            cov3Ds_precomp,
            raster_settings.viewmatrix,
            raster_settings.projmatrix,
            raster_settings.tanfovx,
            raster_settings.tanfovy,
            raster_settings.image_height,
            raster_settings.image_width,
            sh,
            raster_settings.sh_degree,
            camerapos,
            raster_settings.prefiltered,
            raster_settings.debug,
        )

        # Invoke C++/CUDA rasterizer
        if raster_settings.debug:
            cpu_args = cpu_deep_copy_tuple(
                args
            )  # Copy them before they can be corrupted
            try:
                (
                    num_rendered,
                    color,
                    radii,
                    geomBuffer,
                    binningBuffer,
                    imgBuffer,
                    depth
                ) = _C.rasterize_gaussians(*args)
            except Exception as ex:
                torch.save(cpu_args, "snapshot_fw.dump")
                print(
                    "\nAn error occured in forward. Please forward snapshot_fw.dump for debugging."
                )
                raise ex
        else:
            (
                num_rendered,
                color,
                radii,
                geomBuffer,
                binningBuffer,
                imgBuffer,
                depth
            ) = _C.rasterize_gaussians(*args)

        # Keep relevant tensors for backward
        ctx.raster_settings = raster_settings
        ctx.num_rendered = num_rendered
        ctx.save_for_backward(
            colors_precomp,
            means3D,
            scales,
            rotations,
            cov3Ds_precomp,
            radii,
            sh,
            geomBuffer,
            binningBuffer,
            imgBuffer,
            camerapos,
            camerarot,
            depth
        )
        return color, radii, depth
    
    @jaxtyped
    @typechecked
    @staticmethod
    def backward(ctx, grad_out_color, grad_out_radii, grad_out_depth):
        #print(grad_out_color.shape)
        # Restore necessary values from context
        num_rendered = ctx.num_rendered
        raster_settings = ctx.raster_settings
        (
            colors_precomp,
            means3D,
            scales,
            rotations,
            cov3Ds_precomp,
            radii,
            sh,
            geomBuffer,
            binningBuffer,
            imgBuffer,
            camerapos,
            camerarot,
            depth
        ) = ctx.saved_tensors

        # Restructure args as C++ method expects them
        args = (
            raster_settings.bg,
            means3D,
            radii,
            colors_precomp,
            scales,
            rotations,
            raster_settings.scale_modifier,
            raster_settings.cov_offset,
            cov3Ds_precomp,
            raster_settings.viewmatrix,
            raster_settings.projmatrix,
            raster_settings.tanfovx,
            raster_settings.tanfovy,
            grad_out_color,
            grad_out_depth,
            sh,
            raster_settings.sh_degree,
            camerapos,
            geomBuffer,
            num_rendered,
            binningBuffer,
            imgBuffer,
            raster_settings.debug,
        )

        # Compute gradients for relevant tensors by invoking backward method
        if raster_settings.debug:
            cpu_args = cpu_deep_copy_tuple(
                args
            )  # Copy them before they can be corrupted
            try:
                (
                    grad_means2D,
                    grad_colors_precomp,
                    grad_opacities,
                    grad_means3D,
                    grad_cov3Ds_precomp,
                    grad_sh,
                    grad_scales,
                    grad_rotations,
                    grad_camerarot,
                ) = _C.rasterize_gaussians_backward(*args)
            except Exception as ex:
                torch.save(cpu_args, "snapshot_bw.dump")
                print(
                    "\nAn error occured in backward. Writing snapshot_bw.dump for debugging.\n"
                )
                raise ex
        else:
            (
                grad_means2D,
                grad_colors_precomp,
                grad_opacities,
                grad_means3D,
                grad_cov3Ds_precomp,
                grad_sh,
                grad_scales,
                grad_rotations,
                grad_camerarot,
            ) = _C.rasterize_gaussians_backward(*args)

        grads = (
            grad_means3D,
            grad_means2D,
            grad_sh,
            grad_colors_precomp,
            grad_opacities,
            grad_scales,
            grad_rotations,
            grad_cov3Ds_precomp,
            None,
            grad_camerarot,
            None,
        )
        # print(grad_camerarot)

        return grads


class GaussianRasterizer(nn.Module):
    def __init__(self, raster_settings: GaussianRasterizationSettings):
        super().__init__()
        self.raster_settings = raster_settings

    @jaxtyped
    @typechecked
    def markVisible(self, positions):
        # Mark visible points (based on frustum culling for camera) with a boolean
        with torch.no_grad():
            raster_settings = self.raster_settings
            visible = _C.mark_visible(
                positions, raster_settings.viewmatrix, raster_settings.projmatrix
            )

        return visible

    @jaxtyped
    @typechecked
    def forward(
        self,
        means3D: Float[Tensor, "num_gaussians 3"],
        means2D: Float[Tensor, "num_gaussians 3"],
        opacities: Float[Tensor, "num_gaussians 1"],
        shs: Optional[Tensor] = None,
        colors_precomp: Optional[Tensor] = None,
        scales: Optional[Tensor] = None,
        rotations: Optional[Tensor] = None,
        cov3D_precomp: Optional[Tensor] = None,
        camerapos: Optional[Tensor] = None,
        camerarot: Optional[Tensor] = None,
    ):
        raster_settings = self.raster_settings

        if (shs is None and colors_precomp is None) or (
            shs is not None and colors_precomp is not None
        ):
            raise Exception(
                "Please provide excatly one of either SHs or precomputed colors!"
            )

        if ((scales is None or rotations is None) and cov3D_precomp is None) or (
            (scales is not None or rotations is not None) and cov3D_precomp is not None
        ):
            raise Exception(
                "Please provide exactly one of either scale/rotation pair or precomputed 3D covariance!"
            )

        if shs is None:
            shs = torch.Tensor([])
        if colors_precomp is None:
            colors_precomp = torch.Tensor([])

        if scales is None:
            scales = torch.Tensor([])
        if rotations is None:
            rotations = torch.Tensor([])
        if cov3D_precomp is None:
            cov3D_precomp = torch.Tensor([])

        if camerapos is None:
            camerapos = torch.Tensor([])
        if camerarot is None:
            camerarot = torch.Tensor([])

        # Invoke C++/CUDA rasterization routine
        return rasterize_gaussians(
            means3D,
            means2D,
            shs,
            colors_precomp,
            opacities,
            scales,
            rotations,
            cov3D_precomp,
            camerapos,
            camerarot,
            raster_settings,
        )
