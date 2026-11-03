// Qian RGB-domain ISP. Original RTL, informed by the references in RESEARCH.md.
// Centered 3x3 range-gated Gaussian denoise; noise-aware detail; common RGB gain.
// Two RGB+luma line stores; replicated borders; no frame buffering or PS filtering.
// Controls are captured at SOF and travel with the corresponding pixels.
`timescale 1ns/1ps
module rgb_isp_axis #(
    parameter integer IMAGE_WIDTH=800, IMAGE_HEIGHT=480,
    parameter integer RANGE_THRESHOLD=24
)(
    input wire clk, resetn,
    input wire enable, denoise_enable, tone_enable, chroma_enable,
    input wire [23:0] s_data,
    input wire s_valid, s_user, s_last,
    output wire s_ready,
    output reg [23:0] m_data,
    output reg m_valid, m_user, m_last,
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
    reg w_valid,p_valid,q_valid,r_valid,g_valid,h_valid;
    reg w_user,w_last,p_user,p_last,q_user,q_last,r_user,r_last;
    reg g_user,g_last,h_user,h_last;
    reg [3:0] w_flags,p_flags,q_flags;
    reg r_tone,g_tone;
    reg [31:0] w_pixel [0:8];
    reg [23:0] p_pixel [0:8];
    reg [23:0] p_center,q_center,r_rgb,g_rgb,h_rgb;
    reg [7:0] p_luma,q_luma;
    reg [11:0] p_blur,q_blur;
    reg [11:0] q_sum_r,q_sum_g,q_sum_b;
    reg signed [10:0] r_delta;
    reg [8:0] h_gain;
    wire out_ready=!m_valid || m_ready;
    wire h_ready=!h_valid || out_ready;
    wire g_ready=!g_valid || h_ready;
    wire r_ready=!r_valid || g_ready;
    wire q_ready=!q_valid || r_ready;
    wire p_ready=!p_valid || q_ready;
    wire w_ready=!w_valid || p_ready;
    assign s_ready=resetn && state==LOAD && w_ready;
    assign busy=frame_seen || state!=LOAD || w_valid || p_valid || q_valid ||
                r_valid || g_valid || h_valid || m_valid;
    function [7:0] distance8;
        input [7:0] a,b;
        begin distance8=(a>=b)?a-b:b-a; end
    endfunction
    function [23:0] gate_pixel;
        input [23:0] neighbor,center;
        begin
            if(distance8(neighbor[23:16],center[23:16])<=RANGE_THRESHOLD &&
               distance8(neighbor[15:8],center[15:8])<=RANGE_THRESHOLD &&
               distance8(neighbor[7:0],center[7:0])<=RANGE_THRESHOLD)
                gate_pixel=neighbor;
            else gate_pixel=center;
        end
    endfunction
    function [11:0] gaussian_luma;
        input [31:0] a,b,c,d,e,f,g,h,i;
        begin gaussian_luma={4'd0,a[31:24]}+({4'd0,b[31:24]}<<1)+{4'd0,c[31:24]}+
            ({4'd0,d[31:24]}<<1)+({4'd0,e[31:24]}<<2)+({4'd0,f[31:24]}<<1)+
            {4'd0,g[31:24]}+({4'd0,h[31:24]}<<1)+{4'd0,i[31:24]}; end
    endfunction
    function [11:0] gaussian_channel;
        input [7:0] a,b,c,d,e,f,g,h,i;
        begin gaussian_channel={4'd0,a}+({4'd0,b}<<1)+{4'd0,c}+
            ({4'd0,d}<<1)+({4'd0,e}<<2)+({4'd0,f}<<1)+
            {4'd0,g}+({4'd0,h}<<1)+{4'd0,i}; end
    endfunction
    wire [11:0] rounded_blur=q_blur+12'd8;
    wire signed [10:0] detail=$signed({3'd0,q_luma})-$signed({3'd0,rounded_blur[11:4]});
    reg signed [10:0] selected_delta;
    always @(*) begin
        selected_delta=0;
        if(q_flags[0]) begin
            if(q_flags[1]) begin
                // A dead zone and lower gain avoid restoring filtered noise.
                // Suppress sharpening in deep shadows; retain strong edges.
                if(q_luma>=64 && detail>12) selected_delta=(detail-11'sd12)>>>2;
                if(q_luma>=64 && detail< -12) selected_delta= -(((-detail)-11'sd12)>>>2);
                if(selected_delta>4)selected_delta=4;
                if(selected_delta< -4)selected_delta= -4;
            end else begin
                selected_delta=detail>>>1;
                if(detail>=-2 && detail<=2)selected_delta=0;
                if(selected_delta>12)selected_delta=12;
                if(selected_delta< -12)selected_delta= -12;
            end
        end
    end
    wire [11:0] rounded_r=q_sum_r+12'd8;
    wire [11:0] rounded_g=q_sum_g+12'd8;
    wire [11:0] rounded_b=q_sum_b+12'd8;
    wire [7:0] high_rg=(r_rgb[23:16]>r_rgb[15:8])?r_rgb[23:16]:r_rgb[15:8];
    wire [7:0] low_rg=(r_rgb[23:16]<r_rgb[15:8])?r_rgb[23:16]:r_rgb[15:8];
    wire [7:0] high_channel=(high_rg>r_rgb[7:0])?high_rg:r_rgb[7:0];
    wire [7:0] low_channel=(low_rg<r_rgb[7:0])?low_rg:r_rgb[7:0];
    wire signed [10:0] positive_room=$signed({3'd0,(8'd255-high_channel)});
    wire signed [10:0] negative_room=-$signed({3'd0,low_channel});
    wire signed [10:0] color_delta=(r_delta>positive_room)?positive_room:
                                  (r_delta<negative_room)?negative_room:r_delta;
    function [7:0] adjust_channel;
        input [7:0] channel;
        input signed [10:0] adjustment;
        reg signed [11:0] v;
        begin v=$signed({4'd0,channel})+adjustment;
            adjust_channel=(v<0)?0:(v>255)?255:v[7:0]; end
    endfunction
    function [8:0] tone_gain;
        input [7:0] value;
        begin case(value)
            8'd0:tone_gain=9'd256;
            8'd1:tone_gain=9'd384;
            8'd2:tone_gain=9'd384;
            8'd3:tone_gain=9'd384;
            8'd4:tone_gain=9'd384;
            8'd5:tone_gain=9'd384;
            8'd6:tone_gain=9'd384;
            8'd7:tone_gain=9'd384;
            8'd8:tone_gain=9'd384;
            8'd9:tone_gain=9'd384;
            8'd10:tone_gain=9'd384;
            8'd11:tone_gain=9'd384;
            8'd12:tone_gain=9'd384;
            8'd13:tone_gain=9'd384;
            8'd14:tone_gain=9'd384;
            8'd15:tone_gain=9'd384;
            8'd16:tone_gain=9'd384;
            8'd17:tone_gain=9'd384;
            8'd18:tone_gain=9'd384;
            8'd19:tone_gain=9'd384;
            8'd20:tone_gain=9'd384;
            8'd21:tone_gain=9'd384;
            8'd22:tone_gain=9'd384;
            8'd23:tone_gain=9'd384;
            8'd24:tone_gain=9'd384;
            8'd25:tone_gain=9'd384;
            8'd26:tone_gain=9'd384;
            8'd27:tone_gain=9'd384;
            8'd28:tone_gain=9'd384;
            8'd29:tone_gain=9'd384;
            8'd30:tone_gain=9'd384;
            8'd31:tone_gain=9'd384;
            8'd32:tone_gain=9'd384;
            8'd33:tone_gain=9'd384;
            8'd34:tone_gain=9'd383;
            8'd35:tone_gain=9'd381;
            8'd36:tone_gain=9'd379;
            8'd37:tone_gain=9'd377;
            8'd38:tone_gain=9'd375;
            8'd39:tone_gain=9'd373;
            8'd40:tone_gain=9'd371;
            8'd41:tone_gain=9'd369;
            8'd42:tone_gain=9'd367;
            8'd43:tone_gain=9'd365;
            8'd44:tone_gain=9'd364;
            8'd45:tone_gain=9'd362;
            8'd46:tone_gain=9'd361;
            8'd47:tone_gain=9'd359;
            8'd48:tone_gain=9'd358;
            8'd49:tone_gain=9'd356;
            8'd50:tone_gain=9'd355;
            8'd51:tone_gain=9'd353;
            8'd52:tone_gain=9'd352;
            8'd53:tone_gain=9'd351;
            8'd54:tone_gain=9'd349;
            8'd55:tone_gain=9'd348;
            8'd56:tone_gain=9'd347;
            8'd57:tone_gain=9'd345;
            8'd58:tone_gain=9'd344;
            8'd59:tone_gain=9'd343;
            8'd60:tone_gain=9'd342;
            8'd61:tone_gain=9'd341;
            8'd62:tone_gain=9'd340;
            8'd63:tone_gain=9'd339;
            8'd64:tone_gain=9'd338;
            8'd65:tone_gain=9'd336;
            8'd66:tone_gain=9'd335;
            8'd67:tone_gain=9'd334;
            8'd68:tone_gain=9'd333;
            8'd69:tone_gain=9'd332;
            8'd70:tone_gain=9'd332;
            8'd71:tone_gain=9'd331;
            8'd72:tone_gain=9'd330;
            8'd73:tone_gain=9'd329;
            8'd74:tone_gain=9'd328;
            8'd75:tone_gain=9'd327;
            8'd76:tone_gain=9'd326;
            8'd77:tone_gain=9'd325;
            8'd78:tone_gain=9'd324;
            8'd79:tone_gain=9'd324;
            8'd80:tone_gain=9'd323;
            8'd81:tone_gain=9'd322;
            8'd82:tone_gain=9'd321;
            8'd83:tone_gain=9'd320;
            8'd84:tone_gain=9'd320;
            8'd85:tone_gain=9'd319;
            8'd86:tone_gain=9'd318;
            8'd87:tone_gain=9'd317;
            8'd88:tone_gain=9'd317;
            8'd89:tone_gain=9'd316;
            8'd90:tone_gain=9'd315;
            8'd91:tone_gain=9'd315;
            8'd92:tone_gain=9'd314;
            8'd93:tone_gain=9'd313;
            8'd94:tone_gain=9'd313;
            8'd95:tone_gain=9'd312;
            8'd96:tone_gain=9'd311;
            8'd97:tone_gain=9'd311;
            8'd98:tone_gain=9'd310;
            8'd99:tone_gain=9'd309;
            8'd100:tone_gain=9'd309;
            8'd101:tone_gain=9'd308;
            8'd102:tone_gain=9'd307;
            8'd103:tone_gain=9'd307;
            8'd104:tone_gain=9'd306;
            8'd105:tone_gain=9'd306;
            8'd106:tone_gain=9'd305;
            8'd107:tone_gain=9'd305;
            8'd108:tone_gain=9'd304;
            8'd109:tone_gain=9'd303;
            8'd110:tone_gain=9'd303;
            8'd111:tone_gain=9'd302;
            8'd112:tone_gain=9'd302;
            8'd113:tone_gain=9'd301;
            8'd114:tone_gain=9'd301;
            8'd115:tone_gain=9'd300;
            8'd116:tone_gain=9'd300;
            8'd117:tone_gain=9'd299;
            8'd118:tone_gain=9'd299;
            8'd119:tone_gain=9'd298;
            8'd120:tone_gain=9'd298;
            8'd121:tone_gain=9'd297;
            8'd122:tone_gain=9'd297;
            8'd123:tone_gain=9'd296;
            8'd124:tone_gain=9'd296;
            8'd125:tone_gain=9'd295;
            8'd126:tone_gain=9'd295;
            8'd127:tone_gain=9'd294;
            8'd128:tone_gain=9'd294;
            8'd129:tone_gain=9'd293;
            8'd130:tone_gain=9'd293;
            8'd131:tone_gain=9'd292;
            8'd132:tone_gain=9'd292;
            8'd133:tone_gain=9'd292;
            8'd134:tone_gain=9'd291;
            8'd135:tone_gain=9'd291;
            8'd136:tone_gain=9'd290;
            8'd137:tone_gain=9'd290;
            8'd138:tone_gain=9'd289;
            8'd139:tone_gain=9'd289;
            8'd140:tone_gain=9'd289;
            8'd141:tone_gain=9'd288;
            8'd142:tone_gain=9'd288;
            8'd143:tone_gain=9'd287;
            8'd144:tone_gain=9'd287;
            8'd145:tone_gain=9'd287;
            8'd146:tone_gain=9'd286;
            8'd147:tone_gain=9'd286;
            8'd148:tone_gain=9'd285;
            8'd149:tone_gain=9'd285;
            8'd150:tone_gain=9'd285;
            8'd151:tone_gain=9'd284;
            8'd152:tone_gain=9'd284;
            8'd153:tone_gain=9'd284;
            8'd154:tone_gain=9'd283;
            8'd155:tone_gain=9'd283;
            8'd156:tone_gain=9'd282;
            8'd157:tone_gain=9'd282;
            8'd158:tone_gain=9'd282;
            8'd159:tone_gain=9'd281;
            8'd160:tone_gain=9'd281;
            8'd161:tone_gain=9'd281;
            8'd162:tone_gain=9'd280;
            8'd163:tone_gain=9'd280;
            8'd164:tone_gain=9'd280;
            8'd165:tone_gain=9'd279;
            8'd166:tone_gain=9'd279;
            8'd167:tone_gain=9'd279;
            8'd168:tone_gain=9'd278;
            8'd169:tone_gain=9'd278;
            8'd170:tone_gain=9'd278;
            8'd171:tone_gain=9'd277;
            8'd172:tone_gain=9'd277;
            8'd173:tone_gain=9'd277;
            8'd174:tone_gain=9'd276;
            8'd175:tone_gain=9'd276;
            8'd176:tone_gain=9'd276;
            8'd177:tone_gain=9'd275;
            8'd178:tone_gain=9'd275;
            8'd179:tone_gain=9'd275;
            8'd180:tone_gain=9'd274;
            8'd181:tone_gain=9'd274;
            8'd182:tone_gain=9'd274;
            8'd183:tone_gain=9'd274;
            8'd184:tone_gain=9'd273;
            8'd185:tone_gain=9'd273;
            8'd186:tone_gain=9'd273;
            8'd187:tone_gain=9'd272;
            8'd188:tone_gain=9'd272;
            8'd189:tone_gain=9'd272;
            8'd190:tone_gain=9'd272;
            8'd191:tone_gain=9'd271;
            8'd192:tone_gain=9'd271;
            8'd193:tone_gain=9'd271;
            8'd194:tone_gain=9'd270;
            8'd195:tone_gain=9'd270;
            8'd196:tone_gain=9'd270;
            8'd197:tone_gain=9'd270;
            8'd198:tone_gain=9'd269;
            8'd199:tone_gain=9'd269;
            8'd200:tone_gain=9'd269;
            8'd201:tone_gain=9'd268;
            8'd202:tone_gain=9'd268;
            8'd203:tone_gain=9'd268;
            8'd204:tone_gain=9'd268;
            8'd205:tone_gain=9'd267;
            8'd206:tone_gain=9'd267;
            8'd207:tone_gain=9'd267;
            8'd208:tone_gain=9'd267;
            8'd209:tone_gain=9'd266;
            8'd210:tone_gain=9'd266;
            8'd211:tone_gain=9'd266;
            8'd212:tone_gain=9'd266;
            8'd213:tone_gain=9'd265;
            8'd214:tone_gain=9'd265;
            8'd215:tone_gain=9'd265;
            8'd216:tone_gain=9'd265;
            8'd217:tone_gain=9'd264;
            8'd218:tone_gain=9'd264;
            8'd219:tone_gain=9'd264;
            8'd220:tone_gain=9'd264;
            8'd221:tone_gain=9'd263;
            8'd222:tone_gain=9'd263;
            8'd223:tone_gain=9'd263;
            8'd224:tone_gain=9'd263;
            8'd225:tone_gain=9'd262;
            8'd226:tone_gain=9'd262;
            8'd227:tone_gain=9'd262;
            8'd228:tone_gain=9'd262;
            8'd229:tone_gain=9'd262;
            8'd230:tone_gain=9'd261;
            8'd231:tone_gain=9'd261;
            8'd232:tone_gain=9'd261;
            8'd233:tone_gain=9'd261;
            8'd234:tone_gain=9'd260;
            8'd235:tone_gain=9'd260;
            8'd236:tone_gain=9'd260;
            8'd237:tone_gain=9'd260;
            8'd238:tone_gain=9'd260;
            8'd239:tone_gain=9'd259;
            8'd240:tone_gain=9'd259;
            8'd241:tone_gain=9'd259;
            8'd242:tone_gain=9'd259;
            8'd243:tone_gain=9'd258;
            8'd244:tone_gain=9'd258;
            8'd245:tone_gain=9'd258;
            8'd246:tone_gain=9'd258;
            8'd247:tone_gain=9'd258;
            8'd248:tone_gain=9'd257;
            8'd249:tone_gain=9'd257;
            8'd250:tone_gain=9'd257;
            8'd251:tone_gain=9'd257;
            8'd252:tone_gain=9'd257;
            8'd253:tone_gain=9'd256;
            8'd254:tone_gain=9'd256;
            8'd255:tone_gain=9'd256;
        endcase end
    endfunction
    function [8:0] headroom_gain;
        input [7:0] value;
        begin case(value)
            8'd0:headroom_gain=9'd384;
            8'd1:headroom_gain=9'd384;
            8'd2:headroom_gain=9'd384;
            8'd3:headroom_gain=9'd384;
            8'd4:headroom_gain=9'd384;
            8'd5:headroom_gain=9'd384;
            8'd6:headroom_gain=9'd384;
            8'd7:headroom_gain=9'd384;
            8'd8:headroom_gain=9'd384;
            8'd9:headroom_gain=9'd384;
            8'd10:headroom_gain=9'd384;
            8'd11:headroom_gain=9'd384;
            8'd12:headroom_gain=9'd384;
            8'd13:headroom_gain=9'd384;
            8'd14:headroom_gain=9'd384;
            8'd15:headroom_gain=9'd384;
            8'd16:headroom_gain=9'd384;
            8'd17:headroom_gain=9'd384;
            8'd18:headroom_gain=9'd384;
            8'd19:headroom_gain=9'd384;
            8'd20:headroom_gain=9'd384;
            8'd21:headroom_gain=9'd384;
            8'd22:headroom_gain=9'd384;
            8'd23:headroom_gain=9'd384;
            8'd24:headroom_gain=9'd384;
            8'd25:headroom_gain=9'd384;
            8'd26:headroom_gain=9'd384;
            8'd27:headroom_gain=9'd384;
            8'd28:headroom_gain=9'd384;
            8'd29:headroom_gain=9'd384;
            8'd30:headroom_gain=9'd384;
            8'd31:headroom_gain=9'd384;
            8'd32:headroom_gain=9'd384;
            8'd33:headroom_gain=9'd384;
            8'd34:headroom_gain=9'd384;
            8'd35:headroom_gain=9'd384;
            8'd36:headroom_gain=9'd384;
            8'd37:headroom_gain=9'd384;
            8'd38:headroom_gain=9'd384;
            8'd39:headroom_gain=9'd384;
            8'd40:headroom_gain=9'd384;
            8'd41:headroom_gain=9'd384;
            8'd42:headroom_gain=9'd384;
            8'd43:headroom_gain=9'd384;
            8'd44:headroom_gain=9'd384;
            8'd45:headroom_gain=9'd384;
            8'd46:headroom_gain=9'd384;
            8'd47:headroom_gain=9'd384;
            8'd48:headroom_gain=9'd384;
            8'd49:headroom_gain=9'd384;
            8'd50:headroom_gain=9'd384;
            8'd51:headroom_gain=9'd384;
            8'd52:headroom_gain=9'd384;
            8'd53:headroom_gain=9'd384;
            8'd54:headroom_gain=9'd384;
            8'd55:headroom_gain=9'd384;
            8'd56:headroom_gain=9'd384;
            8'd57:headroom_gain=9'd384;
            8'd58:headroom_gain=9'd384;
            8'd59:headroom_gain=9'd384;
            8'd60:headroom_gain=9'd384;
            8'd61:headroom_gain=9'd384;
            8'd62:headroom_gain=9'd384;
            8'd63:headroom_gain=9'd384;
            8'd64:headroom_gain=9'd384;
            8'd65:headroom_gain=9'd384;
            8'd66:headroom_gain=9'd384;
            8'd67:headroom_gain=9'd384;
            8'd68:headroom_gain=9'd384;
            8'd69:headroom_gain=9'd384;
            8'd70:headroom_gain=9'd384;
            8'd71:headroom_gain=9'd384;
            8'd72:headroom_gain=9'd384;
            8'd73:headroom_gain=9'd384;
            8'd74:headroom_gain=9'd384;
            8'd75:headroom_gain=9'd384;
            8'd76:headroom_gain=9'd384;
            8'd77:headroom_gain=9'd384;
            8'd78:headroom_gain=9'd384;
            8'd79:headroom_gain=9'd384;
            8'd80:headroom_gain=9'd384;
            8'd81:headroom_gain=9'd384;
            8'd82:headroom_gain=9'd384;
            8'd83:headroom_gain=9'd384;
            8'd84:headroom_gain=9'd384;
            8'd85:headroom_gain=9'd384;
            8'd86:headroom_gain=9'd384;
            8'd87:headroom_gain=9'd384;
            8'd88:headroom_gain=9'd384;
            8'd89:headroom_gain=9'd384;
            8'd90:headroom_gain=9'd384;
            8'd91:headroom_gain=9'd384;
            8'd92:headroom_gain=9'd384;
            8'd93:headroom_gain=9'd384;
            8'd94:headroom_gain=9'd384;
            8'd95:headroom_gain=9'd384;
            8'd96:headroom_gain=9'd384;
            8'd97:headroom_gain=9'd384;
            8'd98:headroom_gain=9'd384;
            8'd99:headroom_gain=9'd384;
            8'd100:headroom_gain=9'd384;
            8'd101:headroom_gain=9'd384;
            8'd102:headroom_gain=9'd384;
            8'd103:headroom_gain=9'd384;
            8'd104:headroom_gain=9'd384;
            8'd105:headroom_gain=9'd384;
            8'd106:headroom_gain=9'd384;
            8'd107:headroom_gain=9'd384;
            8'd108:headroom_gain=9'd384;
            8'd109:headroom_gain=9'd384;
            8'd110:headroom_gain=9'd384;
            8'd111:headroom_gain=9'd384;
            8'd112:headroom_gain=9'd384;
            8'd113:headroom_gain=9'd384;
            8'd114:headroom_gain=9'd384;
            8'd115:headroom_gain=9'd384;
            8'd116:headroom_gain=9'd384;
            8'd117:headroom_gain=9'd384;
            8'd118:headroom_gain=9'd384;
            8'd119:headroom_gain=9'd384;
            8'd120:headroom_gain=9'd384;
            8'd121:headroom_gain=9'd384;
            8'd122:headroom_gain=9'd384;
            8'd123:headroom_gain=9'd384;
            8'd124:headroom_gain=9'd384;
            8'd125:headroom_gain=9'd384;
            8'd126:headroom_gain=9'd384;
            8'd127:headroom_gain=9'd384;
            8'd128:headroom_gain=9'd384;
            8'd129:headroom_gain=9'd384;
            8'd130:headroom_gain=9'd384;
            8'd131:headroom_gain=9'd384;
            8'd132:headroom_gain=9'd384;
            8'd133:headroom_gain=9'd384;
            8'd134:headroom_gain=9'd384;
            8'd135:headroom_gain=9'd384;
            8'd136:headroom_gain=9'd384;
            8'd137:headroom_gain=9'd384;
            8'd138:headroom_gain=9'd384;
            8'd139:headroom_gain=9'd384;
            8'd140:headroom_gain=9'd384;
            8'd141:headroom_gain=9'd384;
            8'd142:headroom_gain=9'd384;
            8'd143:headroom_gain=9'd384;
            8'd144:headroom_gain=9'd384;
            8'd145:headroom_gain=9'd384;
            8'd146:headroom_gain=9'd384;
            8'd147:headroom_gain=9'd384;
            8'd148:headroom_gain=9'd384;
            8'd149:headroom_gain=9'd384;
            8'd150:headroom_gain=9'd384;
            8'd151:headroom_gain=9'd384;
            8'd152:headroom_gain=9'd384;
            8'd153:headroom_gain=9'd384;
            8'd154:headroom_gain=9'd384;
            8'd155:headroom_gain=9'd384;
            8'd156:headroom_gain=9'd384;
            8'd157:headroom_gain=9'd384;
            8'd158:headroom_gain=9'd384;
            8'd159:headroom_gain=9'd384;
            8'd160:headroom_gain=9'd384;
            8'd161:headroom_gain=9'd384;
            8'd162:headroom_gain=9'd384;
            8'd163:headroom_gain=9'd384;
            8'd164:headroom_gain=9'd384;
            8'd165:headroom_gain=9'd384;
            8'd166:headroom_gain=9'd384;
            8'd167:headroom_gain=9'd384;
            8'd168:headroom_gain=9'd384;
            8'd169:headroom_gain=9'd384;
            8'd170:headroom_gain=9'd384;
            8'd171:headroom_gain=9'd381;
            8'd172:headroom_gain=9'd379;
            8'd173:headroom_gain=9'd377;
            8'd174:headroom_gain=9'd375;
            8'd175:headroom_gain=9'd373;
            8'd176:headroom_gain=9'd370;
            8'd177:headroom_gain=9'd368;
            8'd178:headroom_gain=9'd366;
            8'd179:headroom_gain=9'd364;
            8'd180:headroom_gain=9'd362;
            8'd181:headroom_gain=9'd360;
            8'd182:headroom_gain=9'd358;
            8'd183:headroom_gain=9'd356;
            8'd184:headroom_gain=9'd354;
            8'd185:headroom_gain=9'd352;
            8'd186:headroom_gain=9'd350;
            8'd187:headroom_gain=9'd349;
            8'd188:headroom_gain=9'd347;
            8'd189:headroom_gain=9'd345;
            8'd190:headroom_gain=9'd343;
            8'd191:headroom_gain=9'd341;
            8'd192:headroom_gain=9'd340;
            8'd193:headroom_gain=9'd338;
            8'd194:headroom_gain=9'd336;
            8'd195:headroom_gain=9'd334;
            8'd196:headroom_gain=9'd333;
            8'd197:headroom_gain=9'd331;
            8'd198:headroom_gain=9'd329;
            8'd199:headroom_gain=9'd328;
            8'd200:headroom_gain=9'd326;
            8'd201:headroom_gain=9'd324;
            8'd202:headroom_gain=9'd323;
            8'd203:headroom_gain=9'd321;
            8'd204:headroom_gain=9'd320;
            8'd205:headroom_gain=9'd318;
            8'd206:headroom_gain=9'd316;
            8'd207:headroom_gain=9'd315;
            8'd208:headroom_gain=9'd313;
            8'd209:headroom_gain=9'd312;
            8'd210:headroom_gain=9'd310;
            8'd211:headroom_gain=9'd309;
            8'd212:headroom_gain=9'd307;
            8'd213:headroom_gain=9'd306;
            8'd214:headroom_gain=9'd305;
            8'd215:headroom_gain=9'd303;
            8'd216:headroom_gain=9'd302;
            8'd217:headroom_gain=9'd300;
            8'd218:headroom_gain=9'd299;
            8'd219:headroom_gain=9'd298;
            8'd220:headroom_gain=9'd296;
            8'd221:headroom_gain=9'd295;
            8'd222:headroom_gain=9'd294;
            8'd223:headroom_gain=9'd292;
            8'd224:headroom_gain=9'd291;
            8'd225:headroom_gain=9'd290;
            8'd226:headroom_gain=9'd288;
            8'd227:headroom_gain=9'd287;
            8'd228:headroom_gain=9'd286;
            8'd229:headroom_gain=9'd285;
            8'd230:headroom_gain=9'd283;
            8'd231:headroom_gain=9'd282;
            8'd232:headroom_gain=9'd281;
            8'd233:headroom_gain=9'd280;
            8'd234:headroom_gain=9'd278;
            8'd235:headroom_gain=9'd277;
            8'd236:headroom_gain=9'd276;
            8'd237:headroom_gain=9'd275;
            8'd238:headroom_gain=9'd274;
            8'd239:headroom_gain=9'd273;
            8'd240:headroom_gain=9'd272;
            8'd241:headroom_gain=9'd270;
            8'd242:headroom_gain=9'd269;
            8'd243:headroom_gain=9'd268;
            8'd244:headroom_gain=9'd267;
            8'd245:headroom_gain=9'd266;
            8'd246:headroom_gain=9'd265;
            8'd247:headroom_gain=9'd264;
            8'd248:headroom_gain=9'd263;
            8'd249:headroom_gain=9'd262;
            8'd250:headroom_gain=9'd261;
            8'd251:headroom_gain=9'd260;
            8'd252:headroom_gain=9'd259;
            8'd253:headroom_gain=9'd258;
            8'd254:headroom_gain=9'd257;
            8'd255:headroom_gain=9'd256;
        endcase end
    endfunction
    wire [7:0] gain_high_rg=(g_rgb[23:16]>g_rgb[15:8])?g_rgb[23:16]:g_rgb[15:8];
    wire [7:0] gain_high=(gain_high_rg>g_rgb[7:0])?gain_high_rg:g_rgb[7:0];
    wire [8:0] requested_gain=tone_gain(luma(g_rgb));
    wire [8:0] allowed_gain=headroom_gain(gain_high);
    function [7:0] multiply_gain;
        input [7:0] channel;
        input [8:0] gain_q8;
        reg [17:0] product;
        begin product=channel*gain_q8+18'd128; multiply_gain=product[15:8]; end
    endfunction
    integer k;
    always @(posedge clk) begin
        if(!resetn) begin
            state<=LOAD;x<=0;y<=0;frame_seen<=0;bottom_flush<=0;active_flags<=0;
            top_d1<=0;top_d2<=0;mid_d1<=0;mid_d2<=0;bot_d1<=0;bot_d2<=0;
            w_valid<=0;p_valid<=0;q_valid<=0;r_valid<=0;g_valid<=0;h_valid<=0;m_valid<=0;
            w_user<=0;w_last<=0;p_user<=0;p_last<=0;q_user<=0;q_last<=0;
            r_user<=0;r_last<=0;g_user<=0;g_last<=0;h_user<=0;h_last<=0;m_user<=0;m_last<=0;
            w_flags<=0;p_flags<=0;q_flags<=0;r_tone<=0;g_tone<=0;
            p_center<=0;q_center<=0;r_rgb<=0;g_rgb<=0;h_rgb<=0;m_data<=0;
            p_luma<=0;q_luma<=0;p_blur<=0;q_blur<=0;
            q_sum_r<=0;q_sum_g<=0;q_sum_b<=0;r_delta<=0;h_gain<=256;
            for(k=0;k<9;k=k+1)begin w_pixel[k]<=0;p_pixel[k]<=0;end
        end else begin
            if(out_ready) begin
                m_valid<=h_valid;
                if(h_valid)begin
                    m_data<={multiply_gain(h_rgb[23:16],h_gain),multiply_gain(h_rgb[15:8],h_gain),multiply_gain(h_rgb[7:0],h_gain)};
                    m_user<=h_user;m_last<=h_last;
                end
            end
            if(h_ready)begin
                h_valid<=g_valid;
                if(g_valid)begin
                    h_rgb<=g_rgb;h_user<=g_user;h_last<=g_last;
                    h_gain<=g_tone?((requested_gain<allowed_gain)?requested_gain:allowed_gain):9'd256;
                end
            end
            if(g_ready)begin
                g_valid<=r_valid;
                if(r_valid)begin
                    g_rgb<={adjust_channel(r_rgb[23:16],color_delta),adjust_channel(r_rgb[15:8],color_delta),adjust_channel(r_rgb[7:0],color_delta)};
                    g_tone<=r_tone;g_user<=r_user;g_last<=r_last;
                end
            end
            if(r_ready)begin
                r_valid<=q_valid;
                if(q_valid)begin
                    r_rgb<=(q_flags[1] && !q_flags[3])?{rounded_r[11:4],rounded_g[11:4],rounded_b[11:4]}:q_center;
                    r_delta<=selected_delta;r_tone<=q_flags[2];r_user<=q_user;r_last<=q_last;
                end
            end
            if(q_ready)begin
                q_valid<=p_valid;
                if(p_valid)begin
                    q_center<=p_center;q_luma<=p_luma;q_blur<=p_blur;
                    q_flags<=p_flags;q_user<=p_user;q_last<=p_last;
                    q_sum_r<=gaussian_channel(p_pixel[0][23:16],p_pixel[1][23:16],p_pixel[2][23:16],p_pixel[3][23:16],p_pixel[4][23:16],p_pixel[5][23:16],p_pixel[6][23:16],p_pixel[7][23:16],p_pixel[8][23:16]);
                    q_sum_g<=gaussian_channel(p_pixel[0][15:8],p_pixel[1][15:8],p_pixel[2][15:8],p_pixel[3][15:8],p_pixel[4][15:8],p_pixel[5][15:8],p_pixel[6][15:8],p_pixel[7][15:8],p_pixel[8][15:8]);
                    q_sum_b<=gaussian_channel(p_pixel[0][7:0],p_pixel[1][7:0],p_pixel[2][7:0],p_pixel[3][7:0],p_pixel[4][7:0],p_pixel[5][7:0],p_pixel[6][7:0],p_pixel[7][7:0],p_pixel[8][7:0]);
                end
            end
            if(p_ready)begin
                p_valid<=w_valid;
                if(w_valid)begin
                    p_center<=w_pixel[4][23:0];p_luma<=w_pixel[4][31:24];
                    p_blur<=gaussian_luma(w_pixel[0],w_pixel[1],w_pixel[2],w_pixel[3],w_pixel[4],w_pixel[5],w_pixel[6],w_pixel[7],w_pixel[8]);
                    for(k=0;k<9;k=k+1)p_pixel[k]<=gate_pixel(w_pixel[k][23:0],w_pixel[4][23:0]);
                    p_flags<=w_flags;p_user<=w_user;p_last<=w_last;
                end
            end
            if(w_ready)begin
                w_valid<=0;
                if((state==LOAD && s_valid && s_ready && (frame_seen || s_user)) || state==BOTTOM_ROW)begin
                    if(state==LOAD)begin
                        line2[px]<=center_now;line1[px]<=bottom_now;
                        if(s_user)begin frame_seen<=1;active_flags<={chroma_enable,tone_enable,denoise_enable,enable};end
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
