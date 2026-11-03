// YCoCg local filter: separate chroma smoothing and bounded luma update.
// Centered 3x3 neighborhood, replicated borders and elastic AXIS.
// Original implementation. Flags latched at SOF and carried with pixels.
`timescale 1ns/1ps
module rgb_chroma_axis #(
    parameter integer IMAGE_WIDTH=800, IMAGE_HEIGHT=480, LUMA_UPDATE=1
)(
    input wire clk,resetn,
    input wire [3:0] s_flags,
    input wire [23:0] s_data,
    input wire s_valid,s_user,s_last,
    output wire s_ready,
    output reg [23:0] m_data,
    output reg m_valid,m_user,m_last,
    output reg [3:0] m_flags,
    input wire m_ready,
    output wire busy
);
    localparam integer XB=$clog2(IMAGE_WIDTH), YB=$clog2(IMAGE_HEIGHT);
    localparam LOAD=2'd0, RIGHT_EDGE=2'd1, BOTTOM_ROW=2'd2;
    reg [1:0] state;
    reg [XB-1:0] x;
    reg [YB-1:0] y;
    reg frame_seen, bottom_flush;
    reg [3:0] active_flags;
    wire [XB-1:0] px=(state==LOAD && s_user)?0:x;
    wire [YB-1:0] py=(state==LOAD && s_user)?0:y;
    (* ram_style="distributed" *) reg [31:0] line1 [0:IMAGE_WIDTH-1];
    (* ram_style="distributed" *) reg [31:0] line2 [0:IMAGE_WIDTH-1];
    function [7:0] luma;
        input [23:0] rgb;
        reg [15:0] value;
        begin value=rgb[23:16]*16'd77+rgb[15:8]*16'd150+rgb[7:0]*16'd29;
            luma=value[15:8]; end
    endfunction
    wire [31:0] center_now=line1[px];
    wire [31:0] top_now=(state==LOAD && py==1)?center_now:line2[px];
    wire [31:0] bottom_now=(state==BOTTOM_ROW)?center_now:{luma(s_data),s_data};
    reg [31:0] top_d1,top_d2,mid_d1,mid_d2,bot_d1,bot_d2;
    wire [31:0] top_left=(px==1)?top_d1:top_d2;
    wire [31:0] mid_left=(px==1)?mid_d1:mid_d2;
    wire [31:0] bot_left=(px==1)?bot_d1:bot_d2;

    reg w_valid,t_valid,p_valid,q_valid,v_valid,r_valid;
    reg w_user,w_last,t_user,t_last,p_user,p_last,q_user,q_last;
    reg v_user,v_last,r_user,r_last;
    reg [3:0] w_flags,t_flags,p_flags,q_flags,v_flags,r_flags;
    reg [31:0] w_pixel [0:8];
    reg [7:0] t_y [0:8],p_y [0:8];
    reg signed [9:0] t_co [0:8],t_cg [0:8],p_co [0:8],p_cg [0:8];
    reg [23:0] t_center,p_center,q_center,v_center,r_center;
    reg [7:0] p_lo,p_hi,q_lo,q_hi,q_y;
    reg [11:0] q_sy;
    reg signed [13:0] q_sco,q_scg;
    reg signed [11:0] v_y,v_co,v_cg,r_red,r_green,r_blue;
    wire out_ready=!m_valid || m_ready;
    wire r_ready=!r_valid || out_ready;
    wire v_ready=!v_valid || r_ready;
    wire q_ready=!q_valid || v_ready;
    wire p_ready=!p_valid || q_ready;
    wire t_ready=!t_valid || p_ready;
    wire w_ready=!w_valid || t_ready;
    assign s_ready=resetn && state==LOAD && w_ready;
    assign busy=frame_seen || state!=LOAD || w_valid || t_valid || p_valid ||
                q_valid || v_valid || r_valid || m_valid;
    function signed [9:0] co_value;
        input [23:0] rgb;
        begin co_value=$signed({2'b0,rgb[23:16]})-$signed({2'b0,rgb[7:0]});end
    endfunction
    function signed [9:0] cg_value;
        input [23:0] rgb;
        reg signed [10:0] co,tmp;
        begin
            co=$signed({3'b0,rgb[23:16]})-$signed({3'b0,rgb[7:0]});
            tmp=$signed({3'b0,rgb[7:0]})+(co>>>1);
            cg_value=$signed({3'b0,rgb[15:8]})-tmp;
        end
    endfunction
    function [7:0] y_value;
        input [23:0] rgb;
        reg signed [10:0] co,cg,tmp;
        begin
            co=$signed({3'b0,rgb[23:16]})-$signed({3'b0,rgb[7:0]});
            tmp=$signed({3'b0,rgb[7:0]})+(co>>>1);
            cg=$signed({3'b0,rgb[15:8]})-tmp;
            y_value=tmp+(cg>>>1);
        end
    endfunction
    function accept_neighbor;
        input [7:0] yn,yc;
        input signed [9:0] cn,cc,gn,gc;
        reg signed [10:0] dy,dc,dg;
        begin
            dy=$signed({3'b0,yn})-$signed({3'b0,yc});
            dc=$signed({cn[9],cn})-$signed({cc[9],cc});
            dg=$signed({gn[9],gn})-$signed({gc[9],gc});
            accept_neighbor=(dy>=-16 && dy<=16 && dc>=-40 && dc<=40 && dg>=-32 && dg<=32);
        end
    endfunction
    function [7:0] minimum9;
        input [7:0] a,b,c,d,e,f,g,h,i;
        reg [7:0] z;
        begin z=a;if(b<z)z=b;if(c<z)z=c;if(d<z)z=d;if(e<z)z=e;
            if(f<z)z=f;if(g<z)z=g;if(h<z)z=h;if(i<z)z=i;minimum9=z;end
    endfunction
    function [7:0] maximum9;
        input [7:0] a,b,c,d,e,f,g,h,i;
        reg [7:0] z;
        begin z=a;if(b>z)z=b;if(c>z)z=c;if(d>z)z=d;if(e>z)z=e;
            if(f>z)z=f;if(g>z)z=g;if(h>z)z=h;if(i>z)z=i;maximum9=z;end
    endfunction
    function [11:0] sum_y;
        input [7:0] a,b,c,d,e,f,g,h,i;
        begin sum_y={4'b0,a}+({4'b0,b}<<1)+{4'b0,c}+({4'b0,d}<<1)+
            ({4'b0,e}<<2)+({4'b0,f}<<1)+{4'b0,g}+({4'b0,h}<<1)+{4'b0,i};end
    endfunction
    function signed [13:0] sum_c;
        input signed [9:0] a,b,c,d,e,f,g,h,i;
        begin sum_c=$signed({{4{a[9]}},a})+($signed({{4{b[9]}},b})<<<1)+
            $signed({{4{c[9]}},c})+($signed({{4{d[9]}},d})<<<1)+
            ($signed({{4{e[9]}},e})<<<2)+($signed({{4{f[9]}},f})<<<1)+
            $signed({{4{g[9]}},g})+($signed({{4{h[9]}},h})<<<1)+$signed({{4{i[9]}},i});end
    endfunction
    wire [11:0] rounded_sy=q_sy+12'd8;
    wire signed [11:0] by=$signed({4'b0,rounded_sy[11:4]});
    wire signed [11:0] center_y=$signed({4'b0,q_y});
    wire signed [11:0] dy=(by-center_y)>>>2;
    wire signed [11:0] limited_dy=(dy>2)?12'sd2:(dy< -2)?-12'sd2:dy;
    wire signed [13:0] rounded_co=q_sco+14'sd8,rounded_cg=q_scg+14'sd8;
    wire signed [11:0] recon_t=v_y-(v_cg>>>1);
    wire signed [11:0] recon_b=recon_t-(v_co>>>1);
    function [7:0] clamp8;
        input signed [11:0] value;
        begin clamp8=(value<0)?0:(value>255)?255:value[7:0];end
    endfunction
    integer k;
    always @(posedge clk)begin
        if(!resetn)begin
            state<=LOAD;x<=0;y<=0;frame_seen<=0;bottom_flush<=0;active_flags<=0;
            top_d1<=0;top_d2<=0;mid_d1<=0;mid_d2<=0;bot_d1<=0;bot_d2<=0;
            w_valid<=0;t_valid<=0;p_valid<=0;q_valid<=0;v_valid<=0;r_valid<=0;m_valid<=0;
            w_user<=0;w_last<=0;t_user<=0;t_last<=0;p_user<=0;p_last<=0;q_user<=0;q_last<=0;
            v_user<=0;v_last<=0;r_user<=0;r_last<=0;m_user<=0;m_last<=0;
            w_flags<=0;t_flags<=0;p_flags<=0;q_flags<=0;v_flags<=0;r_flags<=0;m_flags<=0;
            t_center<=0;p_center<=0;q_center<=0;v_center<=0;r_center<=0;m_data<=0;
            p_lo<=0;p_hi<=0;q_lo<=0;q_hi<=0;q_y<=0;q_sy<=0;q_sco<=0;q_scg<=0;
            v_y<=0;v_co<=0;v_cg<=0;r_red<=0;r_green<=0;r_blue<=0;
            for(k=0;k<9;k=k+1)begin
                w_pixel[k]<=0;t_y[k]<=0;t_co[k]<=0;t_cg[k]<=0;
                p_y[k]<=0;p_co[k]<=0;p_cg[k]<=0;
            end
        end else begin
            if(out_ready)begin
                m_valid<=r_valid;
                if(r_valid)begin
                    m_data<=(r_flags[3] && r_flags[1])?{clamp8(r_red),clamp8(r_green),clamp8(r_blue)}:r_center;
                    m_flags<=r_flags;m_user<=r_user;m_last<=r_last;
                end
            end
            if(r_ready)begin
                r_valid<=v_valid;
                if(v_valid)begin
                    r_red<=v_co+recon_b;r_green<=v_cg+recon_t;r_blue<=recon_b;
                    r_center<=v_center;r_flags<=v_flags;r_user<=v_user;r_last<=v_last;
                end
            end
            if(v_ready)begin
                v_valid<=q_valid;
                if(q_valid)begin
                    v_y<=center_y+((LUMA_UPDATE && q_hi-q_lo<=20)?limited_dy:12'sd0);
                    v_co<=rounded_co>>>4;v_cg<=rounded_cg>>>4;
                    v_center<=q_center;v_flags<=q_flags;v_user<=q_user;v_last<=q_last;
                end
            end
            if(q_ready)begin
                q_valid<=p_valid;
                if(p_valid)begin
                    q_sy<=sum_y(p_y[0],p_y[1],p_y[2],p_y[3],p_y[4],p_y[5],p_y[6],p_y[7],p_y[8]);
                    q_sco<=sum_c(p_co[0],p_co[1],p_co[2],p_co[3],p_co[4],p_co[5],p_co[6],p_co[7],p_co[8]);
                    q_scg<=sum_c(p_cg[0],p_cg[1],p_cg[2],p_cg[3],p_cg[4],p_cg[5],p_cg[6],p_cg[7],p_cg[8]);
                    q_lo<=p_lo;q_hi<=p_hi;q_y<=p_y[4];
                    q_center<=p_center;q_flags<=p_flags;q_user<=p_user;q_last<=p_last;
                end
            end
            if(p_ready)begin
                p_valid<=t_valid;
                if(t_valid)begin
                    for(k=0;k<9;k=k+1)begin
                        if(accept_neighbor(t_y[k],t_y[4],t_co[k],t_co[4],t_cg[k],t_cg[4]))begin
                            p_y[k]<=t_y[k];p_co[k]<=t_co[k];p_cg[k]<=t_cg[k];
                        end else begin p_y[k]<=t_y[4];p_co[k]<=t_co[4];p_cg[k]<=t_cg[4];end
                    end
                    p_lo<=minimum9(t_y[0],t_y[1],t_y[2],t_y[3],t_y[4],t_y[5],t_y[6],t_y[7],t_y[8]);
                    p_hi<=maximum9(t_y[0],t_y[1],t_y[2],t_y[3],t_y[4],t_y[5],t_y[6],t_y[7],t_y[8]);
                    p_center<=t_center;p_flags<=t_flags;p_user<=t_user;p_last<=t_last;
                end
            end
            if(t_ready)begin
                t_valid<=w_valid;
                if(w_valid)begin
                    for(k=0;k<9;k=k+1)begin
                        t_y[k]<=y_value(w_pixel[k][23:0]);t_co[k]<=co_value(w_pixel[k][23:0]);t_cg[k]<=cg_value(w_pixel[k][23:0]);
                    end
                    t_center<=w_pixel[4][23:0];t_flags<=w_flags;t_user<=w_user;t_last<=w_last;
                end
            end
            if(w_ready)begin
                w_valid<=0;
                if((state==LOAD && s_valid && s_ready && (frame_seen || s_user)) || state==BOTTOM_ROW)begin
                    if(state==LOAD)begin
                        line2[px]<=center_now;line1[px]<=bottom_now;
                        if(s_user)begin frame_seen<=1;active_flags<=s_flags;end
                    end
                    if(px==0)begin top_d2<=top_now;mid_d2<=center_now;bot_d2<=bottom_now;end
                    else begin top_d2<=top_d1;mid_d2<=mid_d1;bot_d2<=bot_d1;end
                    top_d1<=top_now;mid_d1<=center_now;bot_d1<=bottom_now;
                    if(px!=0 && (py!=0 || state==BOTTOM_ROW))begin
                        w_valid<=1;w_flags<=active_flags;
                        w_pixel[0]<=top_left;w_pixel[1]<=top_d1;w_pixel[2]<=top_now;
                        w_pixel[3]<=mid_left;w_pixel[4]<=mid_d1;w_pixel[5]<=center_now;
                        w_pixel[6]<=bot_left;w_pixel[7]<=bot_d1;w_pixel[8]<=bottom_now;
                        w_user<=(state==LOAD && py==1 && px==1);w_last<=0;
                    end
                    if(px==IMAGE_WIDTH-1)begin
                        x<=0;
                        if(state==LOAD && py==0)y<=1;
                        else begin state<=RIGHT_EDGE;bottom_flush<=(state==BOTTOM_ROW);y<=py;end
                    end else begin x<=px+1'b1;y<=py;end
                end else if(state==RIGHT_EDGE)begin
                    w_valid<=1;w_flags<=active_flags;
                    w_pixel[0]<=top_d2;w_pixel[1]<=top_d1;w_pixel[2]<=top_d1;
                    w_pixel[3]<=mid_d2;w_pixel[4]<=mid_d1;w_pixel[5]<=mid_d1;
                    w_pixel[6]<=bot_d2;w_pixel[7]<=bot_d1;w_pixel[8]<=bot_d1;
                    w_user<=0;w_last<=1;x<=0;
                    if(bottom_flush)begin state<=LOAD;y<=0;frame_seen<=0;end
                    else if(y==IMAGE_HEIGHT-1)state<=BOTTOM_ROW;
                    else begin state<=LOAD;y<=y+1'b1;end
                end
            end

        end
    end
endmodule
