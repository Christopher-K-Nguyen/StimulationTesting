function [File,isQuit] = setArduino(File)
%% Variables
isQuit = false;
bias = File.Parameters.Bias;
baudRate = 9600;
% baudRate = 115200;

%% Function
try
if bias > 0
    % Define the serial object for Arduino
    fprintf('Connecting to Arduino device...');
    startTime = tic;
    arduinoTable = arduinolist;
    if ~isempty(arduinoTable)
        port_cell = str2cell(arduinoTable.Port);
        numofArduinos = length(port_cell);
        if numofArduinos > 1
            port = arduinoTable(1).Port;
            board = arduinoTable(1).Board;
            status = arduinoTable(1).Status;
        else
            port = arduinoTable.Port;
            board = arduinoTable.Board;
            status = arduinoTable.Status;
        end
        fprintf('%s...',board);
        % if contains2(status,'setup required')
        %     fprintf('setting up...');
        %     arduinosetup;
        %     pause();
        % end
        arduinoDevice = serialport(port,baudRate);
        configureTerminator(arduinoDevice,'LF');
        flush(arduinoDevice);
        [endTime,unit] = getEndTime(startTime);
        fprintf('OK (%.2f %s)\n',endTime,unit);
    else
        % Fetch the list of available Bluetooth devices
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
                arduinoDevice = bluetooth(btName,btChannel);
                fprintf('%s...', btName);
                configureTerminator(arduinoDevice,'LF');
                flush(arduinoDevice);
                [endTime,unit] = getEndTime(startTime);
                fprintf('OK (%.2f %s)\n',endTime,unit);
            catch
                isQuit = true;
                [endTime,unit] = getEndTime(startTime);
                fprintf('FAILED (%.2f %s)\n',endTime,unit);
                return;
            end
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
    fprintf('Setting scale...');
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
    [File,~] = getTime(File);
    [File,~] = setOscilloscopeTrigger(File,'video');
    potential = getWaveform(File,1,scopeChannel,false);
    returnOCP = mean(potential);
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
    fprintf('%.3f V to ',bias_diff);
    [wiper,biasDiff_round] = getWiper(bias_diff);
    wiper_use = sprintf('%d',wiper);
    writeline(arduinoDevice,wiper_use);
    [endTime,unit] = getEndTime(startTime);
    fprintf('%.3f V (Wiper Position: %d) (%.2f %s)\n', ...
        biasDiff_round,wiper,endTime,unit);

    fprintf('Getting Arduino serial...');
    % pause(0.5);
    startTime = tic;
    serialLine = readline(arduinoDevice);
    [endTime,unit] = getEndTime(startTime);
    fprintf('%s (%.2f %s)\n',serialLine,endTime,unit);

    %% Store
    File.Arduino.Object = arduinoDevice;
    File.Arduino.Baseline = returnOCP;
    File.Arduino.Bias = biasDiff_round;
    File.Arduino.Wiper = wiper;

else
    File.Arduino.Object = [];
    File.Arduino.Baseline = [];
    File.Arduino.Bias = [];
    File.Arduino.Wiper = [];
end
catch
    fprintf('FAILED\n');
    File.Parameters.Bias = 0;
    File.Arduino.Object = [];
    File.Arduino.Baseline = [];
    File.Arduino.Bias = [];
    File.Arduino.Wiper = [];
end
end