function [File,isQuit] = setArduinoBluetooth(File)
%% Variables
isQuit = false;
bias = File.Parameters.Bias;

%% Set up Bluetooth
if bias > 0
    % Fetch the list of available Bluetooth devices
    fprintf('Find Bluetooth devices...');
    startTime = tic;
    bt_table = bluetoothlist;
    btName_cell = str2cell(bt_table.Name);
    btChannel_cell = str2cell(string(bt_table.Channel));
    btStatus_cell = str2cell(bt_table.Status);
    hc_tf = containsi(btName_cell,'HC');  % Looking for devices named 'HC'
    hc_idx = find(hc_tf);

    % Handle cases with no or multiple devices found
    if isempty(hc_idx)
        isQuit = true;
        [endTime,unit] = getEndTime(startTime);
        fprintf('NONE (%.2f %s)\n',endTime,unit);
        return;
    elseif length(hc_idx) > 1
        bt_idx = hc_idx(1);  % Select the first device if multiple found
    else
        bt_idx = hc_idx;
    end
    btName = btName_cell{bt_idx};
    btChannel = str2double(btChannel_cell{bt_idx});
    btStatus = btStatus_cell{hc_idx};
    [endTime,unit] = getEndTime(startTime);
    fprintf('OK (%.2f %s)\n',endTime,unit);

    % Define and open the Bluetooth connection
    if contains2(btStatus,'require')
        msg = msgbox('HC-05 Bluetooth device is not paired!');
        waitfor(msg);
        isQuit = true;
        return;
    else
        fprintf('\tConnecting to device...')
        startTime = tic;
        try
            bt = bluetooth(btName,btChannel);
            fprintf('%s...', btName);
            configureTerminator(bt,'CR/LF');
            [endTime,unit] = getEndTime(startTime);
            fprintf('OK (%.2f %s)\n',endTime,unit);
        catch
            isQuit = true;
            [endTime,unit] = getEndTime(startTime);
            fprintf('FAILED (%.2f %s)\n',endTime,unit);
            return;
        end
    end

    %% Return Open Circuit Potential
    % Oscilloscope
    numOfDevices = length(File.Oscilloscope);
    for deviceNum = 1:numOfDevices
        channelSelect_cell = File.Oscilloscope(deviceNum).Channels;
        fields_cell = File.Oscilloscope(deviceNum).Fields;
        returnChannel_tf = containsi(fields_cell,{'ret','count'});
        scopeChannel = channelSelect_cell{returnChannel_tf};
        oscilloscope = File.Oscilloscope(deviceNum).Object;
        if any(returnChannel_tf)
            break;
        end
    end

    % Vertical Scale
    fprintf('\tSetting scale...');
    startTime = tic;
    vertScale = 250e-3;
    vertScale_check = 0;
    vertScale_use = sprintf('%s:SCAle %.2e',scopeChannel,vertScale);
    vertScale_query = sprintf('%s:SCAle?',scopeChannel);
    checkTime = tic;
    while ~isequal(vertScale_check,vertScale)
        fprintf(oscilloscope,vertScale_use);  % 200 mV/div vertical scale for voltage
        fprintf(oscilloscope,vertScale_query);
        vertScale_raw = fgetl2(oscilloscope);
        vertScale_check = str2double(vertScale_raw);
        if toc(checkTime) > 1
            break;
        end
    end
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.2e V (%.2f %s)\n',vertScale,endTime,unit);

    % Waveform
    [File,~] = setOscilloscopeTrigger(File,'video');
    [File,~] = getTime(File);
    potential = getWaveform(File,1,scopeChannel,false);
    returnOCP = mean(potential);
    File.Parameters.CounterElectrode.OpenCircuitPotential = returnOCP;
    [File,~] = setOscilloscopeTrigger(File,'edge');

    %% Set Voltage
    fprintf('Setting Arduino voltage...');
    startTime = tic;
    % Set voltage
    if returnOCP > 0
        bias_diff = bias - returnOCP;  % Default voltage setpoint
    else
        bias_diff = 0;
    end
    writeline(bt, num2str(bias_diff));

    % Store Bluetooth object in File for later use
    File.Arduino.Bias = bias_diff;

    [endTime,unit] = getEndTime(startTime);
    fprintf('%.3f V (%.2f %s)\n',bias_diff,endTime,unit);

    %% Store
    File.Arduino.Object = bt;
    File.Arduino.Bias = bias_diff;

else
    File.Arduino.Object = [];
    File.Arduino.Bias = [];
end

end