function [scale_new,pos_new] = setFineScalePos2(File,scopeChannel,YUNits_min,YUNits_max,count)
%%
FINE_SCALE_MIN = 2e-3;
FINE_SCALE_MAX = 5;

%% Function
if ~contains2(scopeChannel,'MATH')
    %% Adjust fine scaling
    fprintf('scaling ');
    numOfDevices = length(File.Oscilloscope);
    for deviceNum = 1:numOfDevices
        channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
        if contains2(channelSelect_cell,scopeChannel)
            break;
        end
    end
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    YUNits_range = abs(YUNits_max - YUNits_min);

    scale = abs(YUNits_range) / 2 / 4;
    if count == 0
        scale_new = scale * 1; %
    else
        scale_new = scale * 1.25 * (count/4 + 1); %
    end
    scale_new = round(scale_new,3,'significant');
    if scale_new < FINE_SCALE_MIN
        if YUNits_range > 2
            scale_fix = 150;
        elseif YUNits_range > 1
            scale_fix = 100;
        elseif YUNits_range > 0.5
            scale_fix = 50;
        else
            scale_fix = 25;
        end
        scale_char = sprintf('%e',scale);
        scale_len = length(scale_char);
        e_idx = strfind(scale_char,'e');
        factor_char = scale_char(1:e_idx-1);
        factor_num = str2double(factor_char);
        scale_factor = ceil(factor_num * 100 + scale_fix) / 100;
        pow_char = scale_char(e_idx:scale_len);
        scale_char = sprintf('%.2f%s',scale_factor,pow_char);
        scale_new = str2double(scale_char) * 1.25 * (count + 1);
        if scale_new < FINE_SCALE_MIN
            scale_new = FINE_SCALE_MIN;
        end
    end

    if  scale_new < FINE_SCALE_MAX
        scale_use = sprintf('%s:SCAle %.2e',scopeChannel,scale_new);
        if isTPS
            fprintf(oscilloscope,'*CLS');
            startTime = tic;
            scale_check = [];
            scale_query = sprintf('%s:SCAle?',scopeChannel);
            while ~isequal(scale_check,scale_new)
                fprintf(oscilloscope,scale_use);
                fprintf(oscilloscope,scale_query);
                scale_raw = fgetl2(oscilloscope);
                scale_check = str2double(scale_raw);
                if toc(startTime) > 0.5
                    break;
                end
            end
        else
            fprintf(oscilloscope,scale_use);
        end
        fprintf('(%.3f V)...',scale_new);

        %% Adjust position
        fprintf('positioning ');
        pos_mean = mean([YUNits_min YUNits_max]);
        pos = pos_mean;
        if pos > 0
            pos_fix = ceil(-pos / scale_new * 10) / 10;
        else
            pos_fix = floor(-pos / scale_new * 10) / 10;
        end
        pos_new = str2double(sprintf('%.2e',pos_fix));
        pos_use = sprintf('%s:POSition %.2e',scopeChannel,pos_new);
        if isTPS
            fprintf(oscilloscope,'*CLS');
            startTime = tic;
            pos_check = [];
            pos_query = sprintf('%s:POSition?',scopeChannel);
            while ~isequal(pos_check,pos_new)
                fprintf(oscilloscope,pos_use);
                fprintf(oscilloscope,pos_query);
                pos_raw = fgetl2(oscilloscope);
                pos_check = str2double(pos_raw);
                if toc(startTime) > 0.5
                    break;
                end
            end
        else
            fprintf(oscilloscope,pos_use);
        end
        fprintf('(%.2f)...',pos_new);
    else
        scale_new = [];
        pos_new = [];
    end
else
    scale_new = [];
    pos_new = [];
    fprintf('failed');
end

end