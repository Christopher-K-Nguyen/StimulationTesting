function setCurrentScale2(File,amplitude1)
%% Variables
% Amplitude
currentStim = abs(amplitude1);

% Environment
% environment = File.Parameters.Environment;
% isAnimal = contains2(environment,'Animal');

% Devices
numOfDevices = length(File.Oscilloscope);
for deviceNum = 1:numOfDevices
    oscilloscope = File.Oscilloscope(deviceNum).Object;
    model = File.Oscilloscope(deviceNum).Model;
    isTPS = contains2(model,'tps');
    channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
    channelName_cell = File.Oscilloscope(deviceNum).ChannelNames;
    current_idx = containsi(channelName_cell,'curr');
    if any(current_idx)
        currentChannel = channelSelect_cell{current_idx};

        %% Function
        fprintf('Setting %s scale...',currentChannel);
        startTime = tic;
        scale = currentStim / 4 * 1e-3;
        scale_char = sprintf('%e',scale);
        scale_len = length(scale_char);
        e_idx = strfind(scale_char,'e');
        factor_char = scale_char(1:e_idx-1);
        factor_num = str2double(factor_char);
        if currentStim <= 5
            scale_factor = (ceil(factor_num * 10) + 60.2) / 10;
        elseif currentStim <= 10
            scale_factor = (ceil(factor_num * 10) + 40.2) / 10;
        elseif currentStim <= 20
            scale_factor = (ceil(factor_num * 10) + 36.2) / 10;
        elseif currentStim <= 40
            scale_factor = (ceil(factor_num * 10) + 28.2) / 10;
        elseif currentStim <= 60
            scale_factor = (ceil(factor_num * 10) + 24.2) / 10;
        elseif currentStim <= 80
            scale_factor = (ceil(factor_num * 10) + 20.2) / 10;
        else
            scale_factor = (ceil(factor_num * 10) + 10.2) / 10;
        end
        pow_char = scale_char(e_idx:scale_len);
        scale_new = sprintf('%.3g%s',scale_factor,pow_char);
        scale_num = str2double(scale_new);
        scale_use = sprintf('%s:SCAle %s',currentChannel,scale_new);
        if isTPS
            scale_check = [];
            scale_query = sprintf('%s:SCAle?',currentChannel);
            checkTime = tic;
            while ~isequal(scale_check,scale_num)
                fprintf(oscilloscope,scale_use);
                fprintf(oscilloscope,scale_query);
                scale_raw = fgetl2(oscilloscope);
                scale_check = str2double(scale_raw);
                if length(checkTime) > 0.5
                    break;
                end
            end
        else
            fprintf(oscilloscope,scale_use);
        end
        % currentChannelUnit = getUnit(File,source);
        currentChannelUnit = 'V';
        [endTime,unit] = getEndTime(startTime);
        % fprintf('%s %s\n',scale_new,unit);
        fprintf('%s %s (%.2f %s)\n',scale_new,currentChannelUnit,endTime,unit);
    end
end

end